<script lang="ts">
	// Prompt styles in a Sheet: tick the ones to apply on the next Generate, and
	// create/edit/delete them. Styles are per profile and shared by all workflows;
	// the backend applies them at submit and records the resulting prompt.
	import { api, apiJson, describeApiError } from '$lib/api';
	import type { Style } from '$lib/contracts';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import type { RunState } from './run.svelte';
	import { liveSelection, toggle } from './styles';

	let { run, open = $bindable(false) }: { run: RunState; open?: boolean } = $props();

	let items = $state.raw<Style[]>([]);
	let error = $state<string | null>(null);
	let busy = $state(false);
	/** null = closed, '' = new, otherwise the id being edited. */
	let editing = $state<string | null>(null);
	let form = $state({ name: '', positive: '', negative: '' });
	const idPrefix = $props.id();

	$effect(() => {
		if (open) void load();
	});

	async function load(): Promise<void> {
		try {
			items = await api<Style[]>('/styles');
			run.styleIds = liveSelection(run.styleIds, items);
		} catch (cause) {
			error = describeApiError(cause);
		}
	}

	function edit(style: Style | null): void {
		editing = style?.id ?? '';
		form = {
			name: style?.name ?? '',
			positive: style?.positive ?? '',
			negative: style?.negative ?? ''
		};
	}

	async function save(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		busy = true;
		error = null;
		try {
			const current = items.find((style) => style.id === editing);
			if (current) {
				await api(`/styles/${current.id}`, {
					method: 'PUT',
					headers: { 'content-type': 'application/json' },
					body: JSON.stringify({ ...form, expected_revision: current.revision })
				});
			} else {
				await apiJson('/styles', 'POST', form);
			}
			editing = null;
		} catch (cause) {
			error = describeApiError(cause);
		} finally {
			busy = false;
			await load(); // a 409 (changed elsewhere) also ends in the truth
		}
	}

	async function remove(style: Style): Promise<void> {
		if (!confirm(`Delete style "${style.name}"?`)) return;
		error = null;
		try {
			await api(`/styles/${style.id}`, { method: 'DELETE' });
		} catch (cause) {
			error = describeApiError(cause);
		}
		await load();
	}
</script>

<Sheet bind:open title="Prompt styles">
	<p class="muted hint">
		Ticked styles are added to the positive and negative prompts when you press Generate. Put
		<code>{'{prompt}'}</code> in a positive style to wrap your text; otherwise it is appended. Ambiguous
		prompt controls are left alone.
	</p>
	{#if error}<p class="error" role="alert">{error}</p>{/if}

	{#if editing !== null}
		<form class="stack" onsubmit={save}>
			<label for={`${idPrefix}-name`}>Name</label>
			<input id={`${idPrefix}-name`} bind:value={form.name} required maxlength="80" />
			<label for={`${idPrefix}-pos`}>Positive</label>
			<textarea id={`${idPrefix}-pos`} bind:value={form.positive} rows="3" maxlength="4000"
			></textarea>
			<label for={`${idPrefix}-neg`}>Negative</label>
			<textarea id={`${idPrefix}-neg`} bind:value={form.negative} rows="3" maxlength="4000"
			></textarea>
			<div class="row">
				<button type="submit" class="btn btn-primary" disabled={busy || !form.name.trim()}
					>Save</button
				>
				<button type="button" class="btn btn-ghost" onclick={() => (editing = null)}>Cancel</button>
			</div>
		</form>
	{:else}
		<button type="button" class="btn btn-primary" onclick={() => edit(null)}>New style</button>
		<ul class="list">
			{#each items as style (style.id)}
				<li class="card item">
					<label class="pick">
						<input
							type="checkbox"
							checked={run.styleIds.includes(style.id)}
							onchange={() => (run.styleIds = toggle(run.styleIds, style.id))}
						/>
						<strong>{style.name}</strong>
					</label>
					{#if style.positive}<p class="muted snippet">+ {style.positive}</p>{/if}
					{#if style.negative}<p class="muted snippet">− {style.negative}</p>{/if}
					<div class="row">
						<button
							type="button"
							class="btn btn-ghost btn-icon"
							aria-label={`Edit ${style.name}`}
							onclick={() => edit(style)}><Icon name="edit" size={18} /></button
						>
						<button
							type="button"
							class="btn btn-ghost btn-icon"
							aria-label={`Delete ${style.name}`}
							onclick={() => remove(style)}><Icon name="trash" size={18} /></button
						>
					</div>
				</li>
			{:else}
				<li class="muted">No styles yet.</li>
			{/each}
		</ul>
	{/if}
</Sheet>

<style>
	.hint,
	.snippet {
		font-size: var(--text-sm);
		overflow-wrap: anywhere;
	}
	.snippet {
		margin: 0;
	}
	.error {
		color: var(--color-danger);
		font-size: var(--text-sm);
	}
	.list {
		list-style: none;
		margin: var(--space-3) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.item {
		padding: var(--space-2) var(--space-3);
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	.pick,
	.row {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
	.stack {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	label {
		font-weight: 600;
		font-size: var(--text-sm);
	}
</style>
