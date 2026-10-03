<script lang="ts">
	import { api, describeApiError } from '$lib/api';
	import { settingsState } from '$lib/settings.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	let {
		workflowId,
		bindingId,
		disabled = false,
		onopen,
		oninsert
	}: {
		workflowId: string;
		bindingId: string;
		disabled?: boolean;
		onopen: () => void;
		oninsert: (text: string) => void;
	} = $props();
	type Prompt = { text: string; created_ms: string };
	let open = $state(false);
	let query = $state('');
	let items = $state.raw<Prompt[]>([]);
	let cursor = $state<string | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let applied = '';
	async function load(reset = true): Promise<void> {
		if (loading) return;
		loading = true;
		error = null;
		if (reset) applied = query;
		try {
			const params = new URLSearchParams({
				workflow_id: workflowId,
				binding_id: bindingId,
				q: applied
			});
			if (!reset && cursor) params.set('cursor', cursor);
			const page = await api<{ items: Prompt[]; next_cursor: string | null }>(
				`/generations/recent-prompts?${params}`
			);
			items = reset ? page.items : [...items, ...page.items];
			cursor = page.next_cursor;
		} catch (cause) {
			error = describeApiError(cause);
		} finally {
			loading = false;
		}
	}
	function show(): void {
		onopen();
		open = true;
		void load();
	}
	function insert(text: string): void {
		open = false;
		oninsert(text);
	}
</script>

{#if settingsState.data?.profile.store_history !== false}
	<button type="button" class="btn btn-ghost history" {disabled} onclick={show}
		>Recent prompts</button
	>
	<Sheet bind:open title="Recent prompts">
		<form
			class="row"
			onsubmit={(event) => {
				event.preventDefault();
				void load();
			}}
		>
			<input
				aria-label="Filter recent prompts"
				bind:value={query}
				maxlength="500"
				placeholder="Find text…"
			/>
			<button type="submit" class="btn" disabled={loading}>Search</button>
		</form>
		<p class="muted">Insert plain text at your cursor, replacing any selected text.</p>
		{#if error}<p role="alert">{error}</p>{/if}
		{#each items as item (item.text)}
			<button type="button" class="btn prompt" title={item.text} onclick={() => insert(item.text)}
				>{item.text}</button
			>
		{/each}
		{#if loading}<p role="status">Loading…</p>
		{:else if items.length === 0}<p class="muted">No saved prompts match this control.</p>{/if}
		{#if cursor}<button type="button" class="btn" disabled={loading} onclick={() => load(false)}
				>Load more</button
			>{/if}
	</Sheet>
{/if}

<style>
	.history {
		margin-top: var(--space-1);
		font-size: var(--text-xs);
	}
	.prompt {
		display: block;
		width: 100%;
		margin-bottom: var(--space-2);
		text-align: left;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		max-height: 9rem;
		overflow: auto;
		scrollbar-gutter: stable;
	}
</style>
