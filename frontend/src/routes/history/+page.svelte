<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { HistoryState } from '$lib/media/history.svelte';
	import { settingsState } from '$lib/settings.svelte';

	const history = new HistoryState();

	onMount(() => {
		history.load();
		history.connect();
		if (!settingsState.data) settingsState.load();
	});
	onDestroy(() => history.dispose());

	function formatDate(value: string): string {
		return new Date(Number(value)).toLocaleString();
	}
</script>

<h1>Generation history</h1>

{#if settingsState.data?.profile.store_history === false}
	<p class="notice">
		History storage is off. Active or uncertain jobs may keep temporary execution snapshots until recovery finishes. Completed jobs keep their media and status, but cannot be reused after their snapshots are purged.
	</p>
{/if}

<section class="clear">
	<h2>Clear reusable history</h2>
	<p>
		This removes saved prompt and workflow snapshots from completed generations you own. It never deletes media or generation ownership/status records, and active, pending, or uncertain jobs are deferred for safe recovery.
	</p>
	<button type="button" onclick={() => history.clear()} disabled={history.clearing}>
		{history.clearing ? 'Clearing…' : 'Clear my reusable history'}
	</button>
	{#if history.clearResult}
		<p role="status">Cleared {history.clearResult.purged}; deferred {history.clearResult.deferred}.</p>
	{/if}
</section>

{#if history.error}<p class="error" role="alert">{history.error}</p>{/if}

{#if history.selectedError}<p class="error" role="alert">{history.selectedError}</p>{/if}
{#if history.selected}
	<section class="detail">
		<h2>Generation details</h2>
		<p><code>{history.selected.id}</code></p>
		<p>Status: <strong>{history.selected.status}</strong></p>
		<p>Output: {history.selected.output_state}</p>
		{#if history.selected.error}
			<pre class="error">{JSON.stringify(history.selected.error, null, 2)}</pre>
		{/if}
		{#if history.selected.effective_values}
			<pre>{JSON.stringify(history.selected.effective_values, null, 2)}</pre>
			{#if history.selected.workflow_id}
				<a href={`/generation/${history.selected.workflow_id}?reuse=${history.selected.id}`}>Reuse as draft</a>
			{/if}
		{:else}
			<p>Prompt and workflow inputs are unavailable for reuse. Media and status are preserved.</p>
		{/if}
	</section>
{/if}

{#if history.loading && history.items.length === 0}
	<p>Loading…</p>
{:else if history.items.length === 0}
	<p>No generations yet.</p>
{:else}
	<ul class="generations">
		{#each history.items as generation (generation.id)}
			<li>
				<button type="button" onclick={() => history.select(generation.id)}>
					<span><strong>{generation.status}</strong> · {generation.output_state}</span>
					<span>{formatDate(generation.created_ms)}</span>
				</button>
			</li>
		{/each}
	</ul>
	{#if history.nextCursor}
		<button class="more" type="button" onclick={() => history.load(false)} disabled={history.loading}>
			{history.loading ? 'Loading…' : 'Load more'}
		</button>
	{/if}
{/if}

<style>
	.notice, .clear, .detail { padding: var(--space-3); margin-bottom: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius); }
	.notice { border-color: var(--color-accent); }
	.clear h2, .detail h2 { margin-top: 0; }
	.clear button, .detail a, .more, .generations button { min-height: var(--touch-target); padding: 0 var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: inherit; }
	.clear button, .more { cursor: pointer; }
	.detail a { display: inline-flex; align-items: center; text-decoration: none; background: var(--color-accent); color: var(--color-accent-text); border: none; }
	.detail pre { white-space: pre-wrap; overflow-wrap: anywhere; }
	.generations { list-style: none; padding: 0; display: flex; flex-direction: column; gap: var(--space-2); }
	.generations button { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: var(--space-2); text-align: left; cursor: pointer; }
	.generations button span:last-child { color: var(--color-text-muted); font-size: 0.875rem; }
	.error { color: var(--color-danger); }
	.more { display: block; margin: var(--space-4) auto 0; background: var(--color-accent); color: var(--color-accent-text); border: none; }
	@media (max-width: 520px) { .generations button { align-items: flex-start; flex-direction: column; padding: var(--space-2) var(--space-3); } }
</style>
