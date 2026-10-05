<script lang="ts">
	import { onMount } from 'svelte';
	import { on } from 'svelte/events';
	import { api, describeApiError } from '$lib/api';
	import type { GenerationDetail, MediaInfo } from '$lib/contracts';
	import Icon from '$lib/ui/Icon.svelte';
	import { isolateInput } from '$lib/ui/isolateInput';
	import { canReuseBoth, diffValues } from './compare';

	let { items, onclose }: { items: [MediaInfo, MediaInfo]; onclose: () => void } = $props();
	let dialog: HTMLDialogElement;
	let differenceButton: HTMLButtonElement;
	let mode = $state<'slider' | 'peek' | 'pair'>('slider');
	let split = $state(50);
	let peekB = $state(false);
	let aspect = $state(1);
	let showDifferences = $state(false);
	let details = $state<(GenerationDetail | null)[]>([null, null]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let mediaErrors = $state<string[]>([]);
	let showSame = $state(false);
	const titleId = $props.id();
	const differenceId = `${titleId}-differences`;
	const canSwipe = $derived(items.every((item) => item.media_kind === 'image'));
	const rows = $derived(
		diffValues(details[0]?.effective_values ?? null, details[1]?.effective_values ?? null)
	);
	const changed = $derived(rows.filter((row) => !row.same).length);
	const shown = $derived(showSame ? rows : rows.filter((row) => !row.same));
	const reusable = $derived(canReuseBoth(details[0], details[1]));

	$effect(() => {
		const targets = items;
		let active = true;
		loading = true;
		error = null;
		details = [null, null];
		Promise.all(
			targets.map((item) =>
				item.generation_id ? api<GenerationDetail>(`/generations/${item.generation_id}`) : null
			)
		)
			.then((result) => {
				if (active) details = result;
			})
			.catch((cause) => {
				if (active) error = describeApiError(cause);
			})
			.finally(() => {
				if (active) loading = false;
			});
		return () => {
			active = false;
		};
	});
	onMount(() => dialog.showModal());

	// object-fit leaves black space inside an image's box. That space also dismisses.
	function insideImage(image: HTMLImageElement, x: number, y: number): boolean {
		if (!image.naturalWidth || !image.naturalHeight) return false;
		const rect = image.getBoundingClientRect();
		const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
		const width = image.naturalWidth * scale;
		const height = image.naturalHeight * scale;
		const left = rect.left + (rect.width - width) / 2;
		const top = rect.top + (rect.height - height) / 2;
		return x >= left && x <= left + width && y >= top && y <= top + height;
	}
	function backdropClick(event: MouseEvent): void {
		const target = event.target as HTMLElement;
		if (target.closest('button, a, input, label, select, .differences, video')) return;
		if (target instanceof HTMLImageElement && insideImage(target, event.clientX, event.clientY))
			return;
		if (target.closest('.composite')) return;
		dialog.close();
	}
	function wipe(element: HTMLDivElement): () => void {
		let pointer: number | null = null;
		const position = (event: PointerEvent): void => {
			const rect = element.getBoundingClientRect();
			split = Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100));
		};
		const detach = [
			on(element, 'pointerdown', (event) => {
				if (event.button !== 0) return;
				const rect = element.getBoundingClientRect();
				const side =
					event.clientX < rect.left + (rect.width * split) / 100 ? '.image-a' : '.image-b';
				const image = element.querySelector<HTMLImageElement>(side);
				if (!image || !insideImage(image, event.clientX, event.clientY)) {
					dialog.close();
					return;
				}
				pointer = event.pointerId;
				element.setPointerCapture(pointer);
				position(event);
			}),
			on(element, 'pointermove', (event) => {
				if (event.pointerId === pointer) position(event);
			}),
			on(element, 'pointerup', () => (pointer = null)),
			on(element, 'pointercancel', () => (pointer = null))
		];
		return () => detach.forEach((remove) => remove());
	}
	function dismissDifferences(): void {
		showDifferences = false;
		differenceButton.focus();
	}
	function failed(item: MediaInfo): void {
		if (!mediaErrors.includes(item.filename)) mediaErrors = [...mediaErrors, item.filename];
	}
	function imageLoaded(event: Event): void {
		const image = event.currentTarget as HTMLImageElement;
		aspect = image.naturalWidth / image.naturalHeight;
	}
</script>

<dialog
	bind:this={dialog}
	aria-labelledby={titleId}
	{@attach (element) => on(element, 'click', backdropClick)}
	{@attach isolateInput}
	{onclose}
>
	<header class="toolbar">
		<h2 id={titleId}>Compare</h2>
		{#if canSwipe}
			<div class="modes" role="group" aria-label="Comparison mode">
				<button
					class="chip"
					type="button"
					aria-pressed={mode === 'slider'}
					onclick={() => (mode = 'slider')}>Slider</button
				>
				<button
					class="chip"
					type="button"
					aria-pressed={mode === 'peek'}
					onclick={() => {
						mode = 'peek';
						peekB = false;
					}}>Hover / tap</button
				>
				<button
					class="chip"
					type="button"
					aria-pressed={mode === 'pair'}
					onclick={() => (mode = 'pair')}>Side by side</button
				>
			</div>
		{/if}
		<button
			type="button"
			class="chip close"
			aria-label="Close comparison"
			onclick={() => dialog.close()}><Icon name="close" /></button
		>
	</header>

	<div class="stage">
		{#if canSwipe && mode !== 'pair'}
			{#if mode === 'slider'}
				<div class="composite" style:--aspect={aspect} {@attach wipe}>
					<img
						class="image-b"
						src={`/api/media/${items[1].id}/file`}
						alt={`B: ${items[1].filename}`}
						draggable="false"
						onerror={() => failed(items[1])}
					/>
					<img
						class="image-a"
						style:clip-path={`inset(0 ${100 - split}% 0 0)`}
						src={`/api/media/${items[0].id}/file`}
						alt={`A: ${items[0].filename}`}
						draggable="false"
						onload={imageLoaded}
						onerror={() => failed(items[0])}
					/>
					<div class="divider" style:left={`${split}%`}>
						<span
							><Icon name="chevron-left" size={16} /><Icon name="chevron-right" size={16} /></span
						>
					</div>
					<span class="image-label a">A</span><span class="image-label b">B</span>
				</div>
			{:else}
				<button
					type="button"
					class="composite peek"
					style:--aspect={aspect}
					aria-label="Switch between image A and B"
					aria-pressed={peekB}
					onpointerenter={(event) => {
						if (event.pointerType === 'mouse') peekB = true;
					}}
					onpointerleave={(event) => {
						if (event.pointerType === 'mouse') peekB = false;
					}}
					onclick={(event) => {
						const image = event.currentTarget.querySelector<HTMLImageElement>(
							peekB ? '.image-b' : '.image-a'
						);
						if (event.detail && image && !insideImage(image, event.clientX, event.clientY))
							dialog.close();
						else peekB = !peekB;
					}}
				>
					<img
						class="image-a"
						class:concealed={peekB}
						src={`/api/media/${items[0].id}/file`}
						alt={`A: ${items[0].filename}`}
						draggable="false"
						onload={imageLoaded}
						onerror={() => failed(items[0])}
					/>
					<img
						class="image-b"
						class:concealed={!peekB}
						src={`/api/media/${items[1].id}/file`}
						alt={`B: ${items[1].filename}`}
						draggable="false"
						onerror={() => failed(items[1])}
					/>
					<span class="image-label a">{peekB ? 'B' : 'A'}</span>
				</button>
			{/if}
		{:else}
			<div class="pair">
				{#each items as item, index (item.id)}
					<figure>
						{#if item.media_kind === 'video'}
							<video
								controls
								muted
								playsinline
								preload="metadata"
								src={`/api/media/${item.id}/file`}
								onerror={() => failed(item)}
							></video>
						{:else}
							<img
								src={`/api/media/${item.id}/file`}
								alt={item.filename}
								onerror={() => failed(item)}
							/>
						{/if}
						<figcaption class="image-label a">{index === 0 ? 'A' : 'B'}</figcaption>
					</figure>
				{/each}
			</div>
		{/if}
	</div>
	{#if mediaErrors.length}<p class="media-error" role="alert">
			Unable to load: {mediaErrors.join(', ')}
		</p>{/if}

	<footer>
		{#if canSwipe && mode === 'slider'}
			<input
				type="range"
				min="0"
				max="100"
				bind:value={split}
				aria-label="Comparison slider position"
			/>
		{:else if canSwipe && mode === 'peek'}
			<span class="hint">Hover to see B · tap or press Enter to switch</span>
		{/if}
		<button
			class="chip differences-toggle"
			type="button"
			bind:this={differenceButton}
			aria-label={showDifferences
				? 'Hide prompt and input differences'
				: 'Show prompt and input differences'}
			aria-expanded={showDifferences}
			aria-controls={differenceId}
			onclick={() => (showDifferences = !showDifferences)}
		>
			<Icon name={showDifferences ? 'chevron-down' : 'chevron-up'} /><span>Differences</span>
		</button>
	</footer>

	<aside
		id={differenceId}
		class="differences"
		class:shown={showDifferences}
		inert={!showDifferences}
		aria-hidden={!showDifferences}
		aria-label="Prompt and input differences"
	>
		<div class="difference-header">
			<h3>Prompt & input differences</h3>
			<button
				class="btn btn-icon"
				type="button"
				aria-label="Hide differences"
				onclick={dismissDifferences}><Icon name="chevron-down" /></button
			>
		</div>
		<div class="difference-content">
			<p class="muted">A: {items[0].filename}<br />B: {items[1].filename}</p>
			{#if loading}<p class="muted" role="status">Loading saved values…</p>{/if}
			{#if error}<p class="error" role="alert">{error}</p>{/if}
			{#if reusable && details[0] && details[1]}
				<div class="row">
					<a class="btn" href={`/generation/${details[0].workflow_id}?reuse=${details[0].id}`}
						>Reuse A</a
					>
					<a class="btn" href={`/generation/${details[1].workflow_id}?reuse=${details[1].id}`}
						>Reuse B</a
					>
				</div>
			{/if}
			{#if rows.length}
				<label class="row"
					><input type="checkbox" bind:checked={showSame} /> Show unchanged ({rows.length -
						changed}) · {changed} changed</label
				>
				{#if !shown.length}<p class="muted">No differences in the saved values.</p>{/if}
				<table>
					<thead
						><tr><th scope="col">Value</th><th scope="col">A</th><th scope="col">B</th></tr></thead
					>
					<tbody>
						{#each shown as row (row.key)}<tr class:diff={!row.same}
								><th scope="row">{row.key}</th><td>{row.a}</td><td>{row.b}</td></tr
							>{/each}
					</tbody>
				</table>
			{:else if !loading && !error}<p class="muted">No saved values to compare.</p>{/if}
		</div>
	</aside>
</dialog>

<style>
	dialog {
		position: fixed;
		inset: 0;
		width: 100%;
		height: 100dvh;
		max-width: none;
		max-height: none;
		margin: 0;
		padding: 0;
		border: 0;
		background: transparent;
		color: white;
		overflow: hidden;
	}
	dialog::backdrop {
		background: rgb(0 0 0 / 0.88);
	}
	.toolbar {
		position: absolute;
		inset: 0 0 auto;
		z-index: 2;
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: max(var(--space-2), env(safe-area-inset-top))
			max(var(--space-2), env(safe-area-inset-right)) var(--space-2)
			max(var(--space-2), env(safe-area-inset-left));
		pointer-events: none;
	}
	h2 {
		margin: 0;
		font-size: var(--text-sm);
	}
	.modes {
		display: flex;
		gap: 0.25rem;
	}
	.chip {
		min-height: 36px;
		border: 1px solid rgb(255 255 255 / 0.25);
		border-radius: var(--radius);
		padding: 0.3rem 0.6rem;
		background: rgb(0 0 0 / 0.55);
		color: white;
		font-size: var(--text-sm);
		cursor: pointer;
		pointer-events: auto;
	}
	.chip[aria-pressed='true'] {
		background: white;
		color: #111;
	}
	.close {
		display: grid;
		place-items: center;
		margin-left: auto;
	}
	.stage {
		position: absolute;
		inset: calc(3.75rem + env(safe-area-inset-top)) env(safe-area-inset-right)
			calc(3.75rem + env(safe-area-inset-bottom)) env(safe-area-inset-left);
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.composite {
		position: relative;
		width: min(
			100%,
			calc(
				(100dvh - 7.5rem - env(safe-area-inset-top) - env(safe-area-inset-bottom)) * var(--aspect)
			)
		);
		aspect-ratio: var(--aspect);
		max-height: 100%;
		touch-action: none;
		user-select: none;
		cursor: ew-resize;
	}
	.composite img {
		position: absolute;
		inset: 0;
	}
	img,
	video {
		width: 100%;
		height: 100%;
		object-fit: contain;
	}
	.peek {
		border: 0;
		padding: 0;
		background: transparent;
		color: white;
		cursor: pointer;
		touch-action: manipulation;
	}
	.concealed {
		visibility: hidden;
	}
	.divider {
		position: absolute;
		top: 0;
		bottom: 0;
		width: 2px;
		background: white;
		pointer-events: none;
	}
	.divider span {
		position: absolute;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		display: flex;
		border-radius: 2rem;
		padding: 0.4rem 0.2rem;
		background: rgb(0 0 0 / 0.7);
		border: 1px solid white;
	}
	.image-label {
		position: absolute;
		top: 0.5rem;
		padding: 0.2rem 0.5rem;
		background: rgb(0 0 0 / 0.55);
		border-radius: var(--radius);
		pointer-events: none;
	}
	.a {
		left: 0.5rem;
	}
	.b {
		right: 0.5rem;
	}
	.pair {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: var(--space-2);
		width: 100%;
		height: 100%;
	}
	figure {
		position: relative;
		margin: 0;
		min-width: 0;
		min-height: 0;
	}
	footer {
		position: absolute;
		inset: auto 0 0;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-2);
		padding: var(--space-2) max(var(--space-2), env(safe-area-inset-right))
			max(var(--space-2), env(safe-area-inset-bottom))
			max(var(--space-2), env(safe-area-inset-left));
		pointer-events: none;
	}
	footer input {
		width: min(45vw, 28rem);
		pointer-events: auto;
	}
	.differences-toggle {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}
	.hint {
		font-size: var(--text-xs);
	}
	.differences {
		position: absolute;
		z-index: 3;
		bottom: calc(3.75rem + env(safe-area-inset-bottom));
		left: 50%;
		width: min(76rem, calc(100% - 1rem));
		max-height: 60dvh;
		display: flex;
		flex-direction: column;
		transform: translate(-50%, calc(100% + 5rem));
		visibility: hidden;
		transition:
			transform 220ms var(--ease),
			visibility 220ms;
		background: var(--color-surface-1);
		color: var(--color-text);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-3);
	}
	.differences.shown {
		transform: translate(-50%, 0);
		visibility: visible;
	}
	.difference-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		padding: var(--space-2);
		border-bottom: 1px solid var(--color-border);
	}
	h3 {
		margin: 0;
		font-size: var(--text-sm);
	}
	.difference-content {
		min-height: 0;
		overflow: auto;
		overscroll-behavior: contain;
		padding: var(--space-2);
		display: grid;
		gap: var(--space-2);
	}
	table {
		width: 100%;
		border-collapse: collapse;
		table-layout: fixed;
		font-size: var(--text-sm);
	}
	td,
	th {
		text-align: left;
		vertical-align: top;
		padding: var(--space-2);
		overflow-wrap: anywhere;
		white-space: pre-wrap;
		border-bottom: 1px solid var(--color-border);
	}
	th:first-child {
		width: 20%;
	}
	tr.diff td {
		background: color-mix(in srgb, var(--color-accent) 15%, transparent);
	}
	.error {
		color: var(--color-danger);
	}
	.media-error {
		position: absolute;
		top: 4rem;
		left: 1rem;
		right: 1rem;
		padding: var(--space-2);
		background: rgb(0 0 0 / 0.75);
		overflow-wrap: anywhere;
	}
	@media (max-width: 600px) {
		h2 {
			display: none;
		}
		.chip {
			font-size: var(--text-xs);
		}
		.pair {
			grid-template-columns: 1fr;
			grid-template-rows: 1fr 1fr;
		}
		.hint {
			max-width: 45vw;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.differences {
			transition: none;
		}
	}
</style>
