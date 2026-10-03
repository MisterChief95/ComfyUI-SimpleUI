<script lang="ts">
	// Workflows: import an API-format ComfyUI export (file picker or drop a .json
	// anywhere on the page), then Run, Design or Delete each workflow.
	import { onMount } from 'svelte';
	import { api, describeApiError } from '$lib/api';
	import type { GraphReplaceResult, Page, WorkflowInfo, WorkflowLayout } from '$lib/contracts';
	import { formatDate, relativeTime } from '$lib/media/format';
	import Icon from '$lib/ui/Icon.svelte';

	let workflows = $state<WorkflowInfo[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);

	let name = $state('');
	let file = $state<File | null>(null);
	let importing = $state(false);
	let importError = $state<string | null>(null);
	let dragging = $state(false);
	let picker = $state<HTMLInputElement>();

	// Inline rename and "Replace graph" (one shared hidden file input, one target card).
	let renamingId = $state<string | null>(null);
	let renameValue = $state('');
	let renameError = $state<string | null>(null);
	let replacePicker = $state<HTMLInputElement>();
	let replaceTarget: WorkflowInfo | null = null;
	let replacingId = $state<string | null>(null);
	/** Outcome of the last replace per workflow id. */
	let outcomes = $state<Record<string, { text: string; review: boolean; error: boolean }>>({});

	let removingId = $state<string | null>(null);
	let removeError = $state<string | null>(null);

	onMount(load);

	async function load(): Promise<void> {
		loading = true;
		loadError = null;
		try {
			const page = await api<Page<WorkflowInfo>>('/workflows?limit=200');
			workflows = page.items;
		} catch (cause) {
			loadError = describeApiError(cause);
		} finally {
			loading = false;
		}
	}

	function choose(next: File | null): void {
		importError = null;
		file = next;
		if (next) name = next.name.replace(/\.[^.]+$/, '');
	}

	function isJson(candidate: File): boolean {
		return candidate.type === 'application/json' || candidate.name.toLowerCase().endsWith('.json');
	}

	function hasFiles(event: DragEvent): boolean {
		return event.dataTransfer?.types.includes('Files') ?? false;
	}

	function ondragover(event: DragEvent): void {
		if (!hasFiles(event)) return;
		event.preventDefault();
		dragging = true;
	}

	function ondrop(event: DragEvent): void {
		if (!hasFiles(event)) return;
		event.preventDefault();
		dragging = false;
		const dropped = event.dataTransfer?.files[0] ?? null;
		if (dropped && !isJson(dropped)) {
			importError = 'Drop a ComfyUI API-format .json file.';
			return;
		}
		choose(dropped);
	}

	async function importWorkflow(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (!file) return;
		importing = true;
		importError = null;
		try {
			const body = await file.text();
			const created = await api<WorkflowInfo>(
				`/workflows?name=${encodeURIComponent(name.trim())}`,
				{
					method: 'POST',
					headers: { 'content-type': 'application/json' },
					body
				}
			);
			name = '';
			file = null;
			if (picker) picker.value = '';
			workflows = [created, ...workflows];
		} catch (cause) {
			importError = describeApiError(cause);
		} finally {
			importing = false;
		}
	}

	function startRename(workflow: WorkflowInfo): void {
		renamingId = workflow.id;
		renameValue = workflow.name;
		renameError = null;
	}

	async function rename(event: SubmitEvent, workflow: WorkflowInfo): Promise<void> {
		event.preventDefault();
		const next = renameValue.trim();
		if (!next || next === workflow.name) {
			renamingId = null;
			return;
		}
		try {
			const updated = await api<WorkflowInfo>(`/workflows/${workflow.id}`, {
				method: 'PATCH',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ name: next })
			});
			workflows = workflows.map((w) => (w.id === updated.id ? updated : w));
			renamingId = null;
		} catch (cause) {
			renameError = describeApiError(cause);
		}
	}

	function pickGraph(workflow: WorkflowInfo): void {
		replaceTarget = workflow;
		replacePicker?.click();
	}

	async function replaceGraph(event: Event & { currentTarget: HTMLInputElement }): Promise<void> {
		const input = event.currentTarget;
		const chosen = input.files?.[0];
		const workflow = replaceTarget;
		input.value = '';
		if (!chosen || !workflow) return;
		replacingId = workflow.id;
		const { [workflow.id]: _previous, ...rest } = outcomes;
		void _previous;
		outcomes = rest;
		try {
			const result = await api<GraphReplaceResult>(`/workflows/${workflow.id}/graph`, {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: await chosen.text()
			});
			workflows = workflows.map((w) => (w.id === workflow.id ? result.workflow : w));
			// Layout entries for bindings the new graph lacks stay in the layout; say so.
			const stale = await api<WorkflowLayout>(`/workflows/${workflow.id}/layout`)
				.then((layout) => layout.stale_bindings.length)
				.catch(() => 0);
			const n = (count: number, noun: string): string =>
				`${count} ${noun}${count === 1 ? '' : 's'}`;
			outcomes = {
				...outcomes,
				[workflow.id]: {
					text:
						`Graph replaced (revision ${result.revision}): ${n(result.added.length, 'control')} added, ` +
						`${result.removed.length} removed.` +
						(stale ? ` ${n(stale, 'saved layout entry')} no longer match.` : ''),
					review: result.removed.length > 0 || stale > 0,
					error: false
				}
			};
		} catch (cause) {
			outcomes = {
				...outcomes,
				[workflow.id]: { text: describeApiError(cause), review: false, error: true }
			};
		} finally {
			replacingId = null;
		}
	}

	async function removeWorkflow(workflow: WorkflowInfo): Promise<void> {
		if (!confirm(`Delete "${workflow.name}"? Its layout and saved presentation go with it.`))
			return;
		removingId = workflow.id;
		removeError = null;
		try {
			await api(`/workflows/${workflow.id}`, { method: 'DELETE' });
			workflows = workflows.filter((w) => w.id !== workflow.id);
		} catch (cause) {
			removeError = describeApiError(cause);
		} finally {
			removingId = null;
		}
	}
</script>

<svelte:window {ondragover} {ondrop} ondragleave={(e) => !e.relatedTarget && (dragging = false)} />

<div class="page stack">
	<h1>Workflows</h1>

	<form class="card import stack" class:dragging onsubmit={importWorkflow}>
		<div class="drop">
			<Icon name="workflows" size={28} />
			<p class="lead">
				{#if file}
					<strong>{file.name}</strong> ready to import
				{:else}
					Drop a ComfyUI <strong>API-format</strong> .json file anywhere on this page
				{/if}
			</p>
			<input
				bind:this={picker}
				class="sr-only"
				id="workflow-file"
				type="file"
				accept="application/json,.json"
				onchange={(e) => choose(e.currentTarget.files?.[0] ?? null)}
			/>
			<label class="btn" for="workflow-file">Choose file</label>
		</div>
		<div class="row wrap fields">
			<div class="grow field">
				<label for="workflow-name">Name</label>
				<input
					id="workflow-name"
					bind:value={name}
					required
					maxlength="120"
					placeholder="Workflow name"
				/>
			</div>
			<button type="submit" class="btn btn-primary" disabled={importing || !file || !name.trim()}>
				{importing ? 'Importing…' : 'Import workflow'}
			</button>
		</div>
		{#if importError}<p class="error" role="alert">{importError}</p>{/if}
	</form>

	{#if loading}
		<p class="muted">Loading…</p>
	{:else if loadError}
		<p class="error" role="alert">{loadError}</p>
	{:else if workflows.length === 0}
		<p class="muted">No workflows yet. Import an API-format ComfyUI export above.</p>
	{:else}
		{#if removeError}<p class="error" role="alert">{removeError}</p>{/if}
		<input
			bind:this={replacePicker}
			class="sr-only"
			type="file"
			accept="application/json,.json"
			aria-label="Replacement graph JSON"
			tabindex="-1"
			onchange={replaceGraph}
		/>
		<ul class="grid">
			{#each workflows as workflow (workflow.id)}
				<li class="card wf">
					{#if renamingId === workflow.id}
						<form class="row rename" onsubmit={(e) => rename(e, workflow)}>
							<input aria-label="Workflow name" bind:value={renameValue} required maxlength="120" />
							<button type="submit" class="btn btn-primary">Save</button>
							<button type="button" class="btn btn-ghost" onclick={() => (renamingId = null)}
								>Cancel</button
							>
						</form>
						{#if renameError}<p class="error" role="alert">{renameError}</p>{/if}
					{:else}
						<div class="row title">
							<h2 title={workflow.name}>{workflow.name}</h2>
							<button
								type="button"
								class="btn btn-ghost btn-icon"
								aria-label={`Rename ${workflow.name}`}
								title="Rename"
								onclick={() => startRename(workflow)}
							>
								<Icon name="edit" size={18} />
							</button>
						</div>
					{/if}
					<p class="meta muted" title={formatDate(workflow.updated_ms)}>
						Updated {relativeTime(workflow.updated_ms)} · revision {workflow.current_revision}
					</p>
					<div class="row wrap actions">
						<a class="btn btn-primary" href={`/generation/${workflow.id}`}>
							<Icon name="generate" size={16} /> Run
						</a>
						<a class="btn" href={`/workflows/${workflow.id}`}>
							<Icon name="design" size={16} /> Design
						</a>
						<button
							type="button"
							class="btn"
							disabled={replacingId === workflow.id}
							title="Upload a new API-format JSON as the next revision"
							onclick={() => pickGraph(workflow)}
						>
							<Icon name="upload" size={16} />
							{replacingId === workflow.id ? 'Replacing…' : 'Replace graph'}
						</button>
						<button
							type="button"
							class="btn btn-ghost btn-icon del"
							aria-label={`Delete ${workflow.name}`}
							title="Delete"
							disabled={removingId === workflow.id}
							onclick={() => removeWorkflow(workflow)}
						>
							<Icon name="trash" size={18} />
						</button>
					</div>
					{#if outcomes[workflow.id]}
						{@const outcome = outcomes[workflow.id]}
						<p
							class={outcome.error ? 'error' : 'outcome'}
							role={outcome.error ? 'alert' : 'status'}
						>
							{outcome.text}
							{#if outcome.review}<a href={`/workflows/${workflow.id}`}>Review in designer</a>{/if}
						</p>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.import {
		--gap: var(--space-3);
		border-style: dashed;
		border-width: 2px;
		border-color: var(--color-border-strong);
		transition:
			border-color 0.12s var(--ease),
			background 0.12s var(--ease);
	}
	.import.dragging {
		border-color: var(--color-accent);
		background: var(--color-accent-soft);
	}
	.drop {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-3);
		color: var(--color-text-muted);
	}
	.lead {
		flex: 1 1 14rem;
		margin: 0;
		overflow-wrap: anywhere;
	}
	#workflow-file:focus-visible + label {
		outline: 2px solid var(--color-accent);
		outline-offset: 2px;
	}
	.fields {
		align-items: flex-end;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		min-width: 12rem;
	}
	.field label {
		font-size: var(--text-sm);
		font-weight: 600;
		color: var(--color-text-muted);
	}
	.error {
		margin: 0;
		color: var(--color-danger);
	}

	.grid {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 17rem), 1fr));
		gap: var(--space-3);
	}
	.wf {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		min-width: 0;
	}
	.title {
		gap: var(--space-1);
	}
	.rename {
		gap: var(--space-1);
	}
	.meta,
	.outcome {
		margin: 0;
		font-size: var(--text-sm);
		overflow-wrap: anywhere;
	}
	.wf h2 {
		flex: 1 1 auto;
		min-width: 0;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.actions {
		margin-top: auto;
	}
	.del {
		margin-left: auto;
		color: var(--color-danger);
	}
</style>
