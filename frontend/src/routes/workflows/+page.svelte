<script lang="ts">
	import { onMount } from 'svelte';
	import { api, describeApiError } from '$lib/api';
	import type { Page, WorkflowInfo } from '$lib/contracts';

	let workflows = $state<WorkflowInfo[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);

	let name = $state('');
	let file = $state<File | null>(null);
	let importing = $state(false);
	let importError = $state<string | null>(null);

	onMount(load);

	async function load(): Promise<void> {
		loading = true;
		loadError = null;
		try {
			const page = await api<Page<WorkflowInfo>>('/workflows');
			workflows = page.items;
		} catch (cause) {
			loadError = describeApiError(cause);
		} finally {
			loading = false;
		}
	}

	function pickFile(event: Event): void {
		file = (event.currentTarget as HTMLInputElement).files?.[0] ?? null;
		if (file && !name) name = file.name.replace(/\.[^.]+$/, '');
	}

	async function importWorkflow(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (!file) return;
		importing = true;
		importError = null;
		try {
			const body = await file.text();
			const created = await api<WorkflowInfo>(`/workflows?name=${encodeURIComponent(name)}`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body
			});
			name = '';
			file = null;
			workflows = [created, ...workflows];
		} catch (cause) {
			importError = describeApiError(cause);
		} finally {
			importing = false;
		}
	}
</script>

<h1>Workflows</h1>

<form onsubmit={importWorkflow} class="import">
	<label>
		Name
		<input bind:value={name} required maxlength="120" />
	</label>
	<label>
		ComfyUI API JSON
		<input type="file" accept="application/json" onchange={pickFile} required />
	</label>
	{#if importError}<p class="error" role="alert">{importError}</p>{/if}
	<button type="submit" disabled={importing || !file}>
		{importing ? 'Importing…' : 'Import workflow'}
	</button>
</form>

{#if loading}
	<p>Loading…</p>
{:else if loadError}
	<p class="error" role="alert">{loadError}</p>
{:else if workflows.length === 0}
	<p>No workflows yet. Import an API-format ComfyUI export above.</p>
{:else}
	<ul class="workflows">
		{#each workflows as workflow (workflow.id)}
			<li>
				<a href={`/generation/${workflow.id}`}>{workflow.name}</a>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.import {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		max-width: 28rem;
		margin-bottom: var(--space-4);
	}

	.import label {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: 0.875rem;
	}

	.import input {
		min-height: var(--touch-target);
		padding: 0 var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-bg);
	}

	.import button {
		min-height: var(--touch-target);
		padding: 0 var(--space-3);
		border: none;
		border-radius: var(--radius);
		background: var(--color-accent);
		color: var(--color-accent-text);
		font-weight: 600;
		cursor: pointer;
	}

	.import button:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.workflows {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.workflows a {
		display: block;
		min-height: var(--touch-target);
		display: flex;
		align-items: center;
		padding: 0 var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		color: inherit;
		text-decoration: none;
	}

	.error {
		color: var(--color-danger);
	}
</style>
