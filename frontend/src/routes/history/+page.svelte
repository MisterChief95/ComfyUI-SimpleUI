<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { MediaQuery } from 'svelte/reactivity';
	import { HistoryState } from '$lib/media/history.svelte';
	import { formatDate, relativeTime } from '$lib/media/format';
	import { workflowNames } from '$lib/media/workflowNames.svelte';
	import { settingsState } from '$lib/settings.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import GenerationSummary from '$lib/media/GenerationSummary.svelte';

	const history = new HistoryState();
	const wide = new MediaQuery('min-width: 1000px');

	let sheetOpen = $state(false);

	onMount(() => {
		history.load();
		history.connect();
		workflowNames.load();
		if (!settingsState.data) settingsState.load();
	});
	onDestroy(() => history.dispose());

	// Phone/tablet: detail opens in a Sheet; wide: a side panel next to the list.
	async function open(id: string): Promise<void> {
		await history.select(id);
		if (!wide.current) sheetOpen = true;
	}

	function statusClass(status: string): string {
		if (status === 'succeeded') return 'badge-success';
		if (status === 'failed') return 'badge-danger';
		if (status === 'running' || status === 'queued') return 'badge-accent';
		if (status === 'submission_unknown' || status === 'unknown') return 'badge-warning';
		return '';
	}

	function errorSummary(error: Record<string, unknown> | null): string | null {
		if (!error) return null;
		const text = error.message ?? error.error ?? error.code ?? JSON.stringify(error);
		return typeof text === 'string' ? text : JSON.stringify(text);
	}
</script>

{#snippet detail()}
	{#if history.selected}
		{@const selected = history.selected}
		<div class="stack" style:--gap="var(--space-2)">
			<GenerationSummary
				detail={selected}
				showIdentity
				statusClass={statusClass(selected.status)}
			/>
			{#if selected.effective_values}
				<pre>{JSON.stringify(selected.effective_values, null, 2)}</pre>
				{#if selected.workflow_id}
					<a
						class="btn btn-primary"
						href={`/generation/${selected.workflow_id}?reuse=${selected.id}`}>Reuse as draft</a
					>
				{/if}
			{:else}
				<p class="muted">
					Prompt and workflow inputs are unavailable for reuse. Media and status are preserved.
				</p>
			{/if}
		</div>
	{/if}
{/snippet}

<div class="page stack">
	<h1>Generation history</h1>

	{#if settingsState.data?.profile.store_history === false}
		<p class="card notice">
			History storage is off. Active or uncertain jobs may keep temporary execution snapshots until
			recovery finishes. Completed jobs keep their media and status, but cannot be reused after
			their snapshots are purged.
		</p>
	{/if}

	{#if history.error}<p class="error" role="alert">{history.error}</p>{/if}
	{#if history.selectedError}<p class="error" role="alert">{history.selectedError}</p>{/if}

	<div class="layout" class:with-panel={wide.current && history.selected}>
		<div class="list stack" style:--gap="var(--space-2)">
			{#if history.loading && history.items.length === 0}
				<p class="muted">Loading…</p>
			{:else if history.items.length === 0}
				<p class="muted">No generations yet.</p>
			{:else}
				<ul>
					{#each history.items as generation (generation.id)}
						{@const summary = errorSummary(generation.error)}
						<li>
							<button
								type="button"
								class="card gen"
								class:selected={history.selected?.id === generation.id}
								onclick={() => open(generation.id)}
							>
								<span class="top">
									<span class="badge {statusClass(generation.status)}">{generation.status}</span>
									<span class="name"
										>{workflowNames.name(generation.workflow_id) ??
											(generation.workflow_id ? 'Deleted workflow' : 'Generation')}</span
									>
									<time class="muted small" title={formatDate(generation.created_ms)}
										>{relativeTime(generation.created_ms)}</time
									>
								</span>
								<span class="muted small">Output: {generation.output_state}</span>
								{#if summary}<span class="err-summary">{summary}</span>{/if}
							</button>
						</li>
					{/each}
				</ul>
				{#if history.nextCursor}
					<button
						class="btn more"
						type="button"
						onclick={() => history.load(false)}
						disabled={history.loading}
					>
						{history.loading ? 'Loading…' : 'Load more'}
					</button>
				{/if}
			{/if}
		</div>

		{#if wide.current && history.selected}
			<aside class="card panel" aria-label="Generation details">
				<h2>Generation details</h2>
				{@render detail()}
			</aside>
		{/if}
	</div>

	<section class="card danger-zone">
		<h2>Clear reusable history</h2>
		<p class="muted">
			This removes saved prompt and workflow snapshots from completed generations you own. It never
			deletes media or generation ownership/status records, and active, pending, or uncertain jobs
			are deferred for safe recovery.
		</p>
		<button
			type="button"
			class="btn btn-danger"
			onclick={() => history.clear()}
			disabled={history.clearing}
		>
			{history.clearing ? 'Clearing…' : 'Clear my reusable history'}
		</button>
		{#if history.clearResult}
			<p role="status">
				Cleared {history.clearResult.purged}; deferred {history.clearResult.deferred}.
			</p>
		{/if}
	</section>
</div>

{#if !wide.current}
	<Sheet bind:open={sheetOpen} title="Generation details">
		{@render detail()}
	</Sheet>
{/if}

<style>
	h1 {
		margin: 0;
	}
	.notice {
		border-color: var(--color-warning);
		background: var(--color-warning-soft);
	}

	.layout {
		display: grid;
		gap: var(--space-3);
		align-items: start;
	}
	.layout.with-panel {
		grid-template-columns: minmax(0, 1fr) minmax(0, 26rem);
	}
	.panel {
		position: sticky;
		top: var(--space-3);
		max-height: calc(100dvh - 2 * var(--space-3));
		overflow-y: auto;
		scrollbar-gutter: stable;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.gen {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		width: 100%;
		min-height: var(--control-h);
		padding: var(--space-2) var(--space-3);
		text-align: left;
		color: inherit;
		font: inherit;
		cursor: pointer;
	}
	.gen:hover {
		border-color: var(--color-border-strong);
	}
	.gen.selected {
		border-color: var(--color-accent);
	}
	.top {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-width: 0;
	}
	.name {
		flex: 1 1 0;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-weight: 600;
	}
	.small {
		font-size: var(--text-sm);
	}
	.err-summary {
		color: var(--color-danger);
		font-size: var(--text-sm);
		overflow: hidden;
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow-wrap: anywhere;
	}
	.more {
		align-self: center;
		margin-top: var(--space-2);
	}

	pre {
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font-family: var(--font-mono);
		font-size: var(--text-sm);
	}
	.error {
		color: var(--color-danger);
	}

	.danger-zone {
		border-color: var(--color-danger);
	}
	.danger-zone h2 {
		color: var(--color-danger);
	}
</style>
