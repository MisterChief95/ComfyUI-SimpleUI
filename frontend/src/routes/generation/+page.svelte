<script lang="ts">
	import { onMount } from 'svelte';
	import { api, describeApiError } from '$lib/api';
	import type { Page, WorkflowInfo } from '$lib/contracts';
	import Icon from '$lib/ui/Icon.svelte';
	import { lastWorkflow } from '$lib/ui/lastWorkflow.svelte';

	let workflows = $state<WorkflowInfo[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);

	onMount(async () => {
		try {
			workflows = (await api<Page<WorkflowInfo>>('/workflows?limit=200')).items;
		} catch (cause) {
			loadError = describeApiError(cause);
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head><title>Generate · SimpleUI</title></svelte:head>

<div class="page stack">
	<h1>Generate</h1>
	{#if loading}
		<p class="muted">Loading…</p>
	{:else if loadError}
		<p class="err" role="alert">{loadError}</p>
	{:else if workflows.length === 0}
		<div class="card stack empty">
			<p>No workflows yet. Import a ComfyUI API JSON file to get started.</p>
			<a class="btn btn-primary" href="/workflows">Go to Workflows</a>
		</div>
	{:else}
		<ul class="grid">
			{#each workflows as workflow (workflow.id)}
				<li>
					<a class="card pick" href={`/generation/${workflow.id}`}>
						<Icon name="generate" />
						<span class="name">{workflow.name}</span>
						{#if workflow.id === lastWorkflow.id}<span class="badge badge-accent">Last used</span>{/if}
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.grid {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
		gap: var(--space-3);
	}
	.pick {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-height: var(--touch-target);
		color: inherit;
		text-decoration: none;
	}
	.pick:hover {
		border-color: var(--color-border-strong);
	}
	.name {
		flex: 1 1 auto;
		min-width: 0;
		overflow-wrap: anywhere;
		font-weight: 600;
	}
	.empty {
		align-items: flex-start;
	}
	.err {
		color: var(--color-danger);
	}
</style>
