<script lang="ts">
	// Latest result (or a picked recent output), live progress, errors, and the
	// recent-outputs strip with "Reuse settings".
	import Icon from '$lib/ui/Icon.svelte';
	import Thumbnail from '$lib/media/Thumbnail.svelte';
	import { api, describeApiError } from '$lib/api';
	import type { GenerationDetail } from '$lib/contracts';
	import Viewer from '$lib/media/Viewer.svelte';
	import { GalleryState } from '$lib/media/gallery.svelte';
	import { openViewer } from '$lib/media/openViewer';
	import type { RunState } from './run.svelte';
	import { describeGenerationError, isTerminal, statusInfo } from './status';

	let { run }: { run: RunState } = $props();

	const tracker = $derived(run.tracker);
	const latest = $derived(tracker.latest);
	const shown = $derived(tracker.shown);
	const outputs = $derived(
		shown?.generation_id === latest?.id ? tracker.outputs : tracker.pickedOutputs
	);
	const outputIndex = $derived(outputs.findIndex((item) => item.id === shown?.id));
	const info = $derived(statusInfo(latest));
	const failure = $derived(
		latest && latest.status !== 'cancelled' ? describeGenerationError(latest.error) : null
	);
	const nodeLabel = $derived.by(() => {
		const id = tracker.nodeId;
		if (id === null) return null;
		const control = run.schema?.controls.find((c) => c.node_id === id);
		return control ? `${control.class_type} #${id}` : `node ${id}`;
	});
	// "node X of Y" from progress_state; the bar follows the sampler step when
	// there is one, else the share of finished nodes.
	const nodePos = $derived(
		tracker.nodes
			? `node ${Math.min(tracker.nodes.done + 1, tracker.nodes.total)} of ${tracker.nodes.total}`
			: null
	);
	const percent = $derived(
		tracker.progress
			? Math.round((tracker.progress.value / tracker.progress.max) * 100)
			: tracker.nodes
				? Math.round((tracker.nodes.done / tracker.nodes.total) * 100)
				: null
	);
	// A latent preview stands in for the media until the job ends.
	const livePreview = $derived(tracker.active ? tracker.preview : null);
	const empty = $derived(!latest && !shown);

	// Lightbox over the recent-outputs strip (plus the shown item if it is not in it).
	const lightbox = new GalleryState();
	function openLightbox(event: MouseEvent): void {
		if (!shown) return;
		const item = shown;
		lightbox.items = tracker.recent.some((item) => item.id === shown.id)
			? tracker.recent
			: [shown, ...tracker.recent];
		const source = (event.currentTarget as HTMLButtonElement).querySelector('img');
		void openViewer(source, () => void lightbox.select(item));
	}
	let copyMessage = $state('');
	let copying = $state(false);

	async function copySummary(): Promise<void> {
		const id = shown?.generation_id ?? latest?.id;
		if (!id) return;
		copyMessage = '';
		copying = true;
		try {
			const generation =
				id === latest?.id ? latest : await api<GenerationDetail>(`/generations/${id}`);
			const values = generation.effective_values;
			const seeds = Object.fromEntries(
				(run.schema?.controls ?? [])
					.filter(
						(control) => control.component === 'seed' && values && control.binding_id in values
					)
					.map((control) => [control.binding_id, values?.[control.binding_id]])
			);
			await navigator.clipboard.writeText(
				JSON.stringify(
					{
						workflow: { id: generation.workflow_id, name: run.name },
						generation: generation.id,
						status: generation.status,
						output_state: generation.output_state,
						seeds,
						values
					},
					null,
					2
				)
			);
			copyMessage = 'Generation summary copied.';
		} catch (cause) {
			copyMessage = !navigator.clipboard
				? 'Clipboard needs HTTPS or localhost.'
				: describeApiError(cause);
		} finally {
			copying = false;
		}
	}
</script>

<div class="panel" class:empty>
	<div class="panel-head">
		<span class="panel-title"><Icon name="image" size={16} /> Output</span>
		{#if latest && !isTerminal(latest.status)}
			<div class="progress" role="status">
				<span class={`badge badge-${info.kind}`}>{info.label}</span>
				<span class="muted grow node">
					{#if nodeLabel}{nodeLabel}{:else if latest.status === 'queued'}Waiting in the ComfyUI
						queue{/if}{#if nodePos}{nodeLabel ? ' · ' : ''}{nodePos}{/if}
				</span>
				{#if tracker.progress}
					<span class="muted steps">{tracker.progress.value}/{tracker.progress.max}</span>
				{/if}
				<progress
					max="100"
					value={percent ?? undefined}
					aria-label="Generation progress"
					aria-valuetext={percent === null ? 'In progress' : `${percent}%`}
				></progress>
			</div>
		{/if}
		<a
			class="btn btn-ghost btn-icon"
			href="/gallery"
			aria-label="Open gallery"
			title="Open gallery"
		>
			<Icon name="external" size={16} />
		</a>
	</div>

	{#if failure}
		<div class="failure" role="alert">
			<strong
				>{latest?.status === 'failed' ? 'The generation failed' : 'Something went wrong'}</strong
			>
			{#each failure.summary as line (line)}<p>{line}</p>{/each}
			<details>
				<summary>Details</summary>
				<pre>{failure.details}</pre>
			</details>
		</div>
	{/if}

	{#if livePreview}
		<div class="viewer">
			<img src={livePreview} alt="Live preview of the running generation" />
		</div>
		<p class="muted notice" role="status">
			Live preview. The saved result replaces it when the job finishes.
		</p>
	{:else if shown}
		<div class="viewer">
			{#if shown.state === 'unavailable'}
				<p class="muted missing">This file is unavailable. Its record is preserved.</p>
			{:else if shown.media_kind === 'image'}
				<button type="button" class="zoom-open" aria-label="View full size" onclick={openLightbox}>
					<img src={`/api/media/${shown.id}/file`} alt={shown.filename} />
				</button>
			{:else if shown.media_kind === 'video'}
				<!-- svelte-ignore a11y_media_has_caption -->
				<video controls preload="metadata" src={`/api/media/${shown.id}/file`}></video>
			{:else}
				<a class="btn" href={`/api/media/${shown.id}/download`}>Download {shown.filename}</a>
			{/if}
		</div>
		{#if outputIndex >= 0 && outputs.length > 1}
			<nav class="row output-nav" aria-label="Generation outputs">
				<button
					type="button"
					class="btn"
					aria-label="Previous output"
					disabled={outputIndex === 0}
					onclick={() => void tracker.pick(outputs[outputIndex - 1])}
					><Icon name="chevron-left" size={16} /></button
				>
				<span class="muted" role="status">Output {outputIndex + 1} of {outputs.length}</span>
				<button
					type="button"
					class="btn"
					aria-label="Next output"
					disabled={outputIndex === outputs.length - 1}
					onclick={() => void tracker.pick(outputs[outputIndex + 1])}
					><Icon name="chevron-right" size={16} /></button
				>
			</nav>
		{/if}
		<div class="row wrap tools">
			<button
				type="button"
				class="btn"
				disabled={!shown.generation_id}
				onclick={() => shown.generation_id && run.reuse(shown.generation_id)}
			>
				<Icon name="reset" size={16} /> Reuse settings
			</button>
			<button
				type="button"
				class="btn"
				disabled={!shown.generation_id || copying}
				onclick={() => void copySummary()}
			>
				<Icon name="copy" size={16} /> Copy generation summary
			</button>
			{#if tracker.picked && tracker.outputs[0] && tracker.picked.id !== tracker.outputs[0].id}
				<button type="button" class="btn btn-ghost" onclick={() => (tracker.picked = null)}
					>Show latest</button
				>
			{/if}
			<span class="muted notice" role="status">{run.notice ?? ''}</span>
		</div>
	{:else if empty}
		<div class="empty-state">
			<span class="empty-icon"><Icon name="image" size={32} /></span>
			<strong>Your next result starts here</strong>
			<p class="muted none">Choose your settings and generate. Your results appear here.</p>
		</div>
	{:else if latest && isTerminal(latest.status) && !failure}
		<p class="muted none">No output was saved for this run.</p>
	{/if}
	{#if !shown && latest && !livePreview}
		<button type="button" class="btn" disabled={copying} onclick={() => void copySummary()}>
			<Icon name="copy" size={16} /> Copy generation summary
		</button>
	{/if}
	{#if copyMessage}<p class="muted notice" role="status">{copyMessage}</p>{/if}

	{#if run.seedResults.length}
		<div class="seeds" role="group" aria-label="Seed of the latest generation">
			{#each run.seedResults as { control, value } (control.binding_id)}
				<div class="row wrap">
					<span class="muted label">{control.label}</span>
					<code class="grow">{value}</code>
					<button
						type="button"
						class="btn"
						disabled={String(run.valueFor(control)) === value}
						title="Set this seed in the controls"
						onclick={() => run.setValue(control, value)}
					>
						Use this seed
					</button>
				</div>
			{/each}
		</div>
	{/if}

	{#if tracker.recent.length}
		<div class="strip" role="group" aria-label="Recent outputs">
			{#each tracker.recent as item (item.id)}
				<button
					type="button"
					class="thumb"
					aria-label={`Show ${item.filename}`}
					aria-pressed={shown?.id === item.id}
					onclick={() => void tracker.pick(item)}
				>
					<Thumbnail {item} />
				</button>
			{/each}
		</div>
	{/if}
</div>

{#if lightbox.selected}
	<Viewer gallery={lightbox} />
{/if}

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		min-width: 0;
	}
	.panel.empty {
		gap: var(--space-3);
	}
	.panel-head {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		flex: none;
	}
	.panel-title {
		flex: none;
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		font-weight: 650;
		font-size: var(--text-sm);
	}
	.empty-state {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: var(--space-2);
		text-align: center;
		padding: var(--space-3);
	}
	.empty-icon {
		display: grid;
		place-items: center;
		width: 3rem;
		height: 3rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		color: var(--color-text-faint);
	}
	.none {
		margin: 0;
		font-size: var(--text-sm);
	}
	.progress {
		display: flex;
		align-items: center;
		flex: 1;
		min-width: 0;
		gap: var(--space-2);
	}
	.steps {
		flex: none;
		font-size: var(--text-sm);
		white-space: nowrap;
	}
	.node {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: var(--text-sm);
	}
	progress {
		position: absolute;
		left: 0;
		bottom: -0.25rem;
		width: 100%;
		height: 0.25rem;
		accent-color: var(--color-accent);
	}
	.failure {
		padding: var(--space-3);
		background: var(--color-danger-soft);
		border-radius: var(--radius);
		color: var(--color-danger);
		font-size: var(--text-sm);
	}
	.failure p {
		margin: var(--space-1) 0 0;
		overflow-wrap: anywhere;
	}
	.failure pre {
		margin: var(--space-2) 0 0;
		max-height: 14rem;
		overflow: auto;
		scrollbar-gutter: stable;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		color: var(--color-text-muted);
	}
	.viewer {
		display: flex;
		justify-content: center;
		background: var(--color-surface-2);
		border-radius: var(--radius);
		overflow: hidden;
	}
	.viewer img,
	.viewer video {
		display: block;
		max-width: 100%;
		max-height: 45dvh;
		object-fit: contain;
	}
	.zoom-open {
		padding: 0;
		border: 0;
		background: none;
		cursor: zoom-in;
		line-height: 0;
	}
	.missing {
		padding: var(--space-4) var(--space-3);
		margin: 0;
	}
	.tools {
		min-height: var(--control-h);
	}
	.output-nav {
		justify-content: center;
	}
	.notice {
		font-size: var(--text-xs);
	}
	.seeds {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		font-size: var(--text-sm);
	}
	.seeds .label {
		flex: 1 1 100%;
	}
	.seeds code {
		min-width: 0;
		overflow-wrap: anywhere;
		font-family: var(--font-mono);
	}
	.strip {
		display: flex;
		gap: var(--space-2);
		overflow-x: auto;
		padding-bottom: var(--space-1);
	}
	.thumb {
		flex: none;
		display: grid;
		place-items: center;
		width: 4rem;
		height: 4rem;
		padding: 0;
		color: var(--color-text-muted);
		background: var(--color-surface-2);
		border: 2px solid transparent;
		border-radius: var(--radius);
		overflow: hidden;
		cursor: pointer;
	}
	.thumb[aria-pressed='true'] {
		border-color: var(--color-accent);
	}

	/* Tablet/desktop: the panel fills the aside's height with no scroll. The image fills the
	   width, bounded by the height left after the toolbars; the recent strip takes the rest. */
	@media (min-width: 768px) {
		.panel {
			height: 100%;
			min-height: 0;
		}
		.viewer {
			flex: none;
		}
		.viewer img,
		.viewer video {
			width: 100%;
			height: auto;
			max-height: max(6rem, calc(100cqh - 17rem));
		}
		.strip {
			flex: 1 1 0;
			min-height: 4rem;
			display: grid;
			grid-template-columns: repeat(auto-fill, minmax(4rem, 1fr));
			grid-auto-rows: min-content;
			align-content: start;
			overflow-x: hidden;
			overflow-y: auto;
			scrollbar-gutter: stable;
		}
		.thumb {
			width: auto;
			height: auto;
			aspect-ratio: 1;
		}
	}
</style>
