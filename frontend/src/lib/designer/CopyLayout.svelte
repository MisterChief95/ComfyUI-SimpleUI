<script lang="ts">
	// "Copy layout from…": pick another workflow; its saved layout replaces the
	// working one as an unsaved, undoable edit (bindings missing here are dropped).
	import { onMount } from 'svelte';
	import { api, describeApiError } from '$lib/api';
	import type { Page, WorkflowInfo } from '$lib/contracts';
	import Sheet from '$lib/ui/Sheet.svelte';
	import type { Designer } from './editor.svelte';

	let { editor, open = $bindable(false) }: { editor: Designer; open?: boolean } = $props();

	let others = $state<WorkflowInfo[] | null>(null);
	let busyId = $state<string | null>(null);
	let message = $state<string | null>(null);

	// Mounted only while the sheet is open, so the list is fresh every time.
	onMount(() => {
		api<Page<WorkflowInfo>>('/workflows?limit=200')
			.then((page) => (others = page.items.filter((w) => w.id !== editor.workflowId)))
			.catch((cause) => (message = describeApiError(cause)));
	});

	async function copy(source: WorkflowInfo): Promise<void> {
		busyId = source.id;
		message = await editor.copyLayoutFrom(source);
		busyId = null;
		if (message === null) open = false;
	}
</script>

<Sheet bind:open title="Copy layout from…">
	<p class="muted hint">
		Replaces the sections below with the other workflow's saved layout, keeping only controls that
		exist here. Nothing is saved until you press Save, and Undo brings your layout back.
	</p>
	{#if message}<p class="error" role="alert">{message}</p>{/if}
	{#if others === null}
		{#if !message}<p class="muted">Loading…</p>{/if}
	{:else if others.length === 0}
		<p class="muted">There are no other workflows to copy from.</p>
	{:else}
		<ul class="list">
			{#each others as workflow (workflow.id)}
				<li>
					<button
						type="button"
						class="btn pick"
						disabled={busyId !== null}
						onclick={() => copy(workflow)}
					>
						{busyId === workflow.id ? 'Copying…' : workflow.name}
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</Sheet>

<style>
	.hint {
		margin: 0 0 var(--space-3);
		font-size: var(--text-sm);
	}
	.error {
		margin: 0 0 var(--space-3);
		color: var(--color-danger);
		font-size: var(--text-sm);
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.pick {
		width: 100%;
		justify-content: flex-start;
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
