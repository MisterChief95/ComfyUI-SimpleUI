<script lang="ts">
	// Presets of this workflow in a Sheet (bottom sheet on phones, drawer wider):
	// save the current draft, apply, overwrite, rename, delete.
	import type { Preset } from '$lib/contracts';
	import { relativeTime } from '$lib/media/format';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import type { RunState } from './run.svelte';

	let { run, open = $bindable(false) }: { run: RunState; open?: boolean } = $props();

	const presets = $derived(run.presets);
	const changed = $derived(Object.keys(presets.currentValues).length);
	const cannotSave = $derived(
		run.invalid.size > 0
			? 'Fix the invalid values first.'
			: changed === 0
				? 'Change a control first: presets store only changed values.'
				: null
	);

	let newName = $state('');
	let editingId = $state<string | null>(null);
	let editName = $state('');
	const nameId = $props.id();

	async function create(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (await presets.create(newName.trim())) newName = '';
	}

	function startRename(preset: Preset): void {
		editingId = preset.id;
		editName = preset.name;
	}

	async function rename(event: SubmitEvent, preset: Preset): Promise<void> {
		event.preventDefault();
		const name = editName.trim();
		if (name && name !== preset.name && !(await presets.rename(preset, name))) return;
		editingId = null;
	}

	function apply(preset: Preset): void {
		presets.apply(preset);
		open = false;
	}

	function remove(preset: Preset): void {
		if (confirm(`Delete preset "${preset.name}"?`)) void presets.remove(preset);
	}

	function overwrite(preset: Preset): void {
		if (
			confirm(
				`Replace the values of "${preset.name}" with your ${changed} current changed value(s)?`
			)
		) {
			void presets.overwrite(preset);
		}
	}
</script>

<Sheet bind:open title="Presets">
	<form class="stack save" onsubmit={create}>
		<label for={nameId}>Save current as preset</label>
		<div class="row">
			<input
				id={nameId}
				bind:value={newName}
				required
				maxlength="80"
				placeholder="Preset name"
				autocomplete="off"
			/>
			<button
				type="submit"
				class="btn btn-primary"
				disabled={!newName.trim() || cannotSave !== null || presets.busy !== null}
			>
				{presets.busy === 'new' ? 'Saving…' : 'Save'}
			</button>
		</div>
		<p class="hint muted">
			{cannotSave ??
				`Stores the ${changed} control${changed === 1 ? '' : 's'} you changed. Unchanged controls stay at the imported value.`}
		</p>
	</form>

	{#if presets.error}<p class="error" role="alert">{presets.error}</p>{/if}

	{#if presets.loading && presets.items.length === 0}
		<p class="muted">Loading…</p>
	{:else if presets.items.length === 0}
		<p class="muted">No presets yet.</p>
	{:else}
		<ul class="list">
			{#each presets.items as preset (preset.id)}
				<li class="card item">
					{#if editingId === preset.id}
						<form class="row" onsubmit={(e) => rename(e, preset)}>
							<input aria-label="Preset name" bind:value={editName} required maxlength="80" />
							<button type="submit" class="btn btn-primary" disabled={presets.busy !== null}
								>Save</button
							>
							<button type="button" class="btn btn-ghost" onclick={() => (editingId = null)}
								>Cancel</button
							>
						</form>
					{:else}
						<div class="head">
							<strong title={preset.name}>{preset.name}</strong>
							<span class="muted meta">
								{Object.keys(preset.values).length} value{Object.keys(preset.values).length === 1
									? ''
									: 's'} · {relativeTime(preset.updated_ms)}
							</span>
						</div>
						<div class="row wrap actions">
							<button
								type="button"
								class="btn btn-primary"
								disabled={presets.busy !== null}
								onclick={() => apply(preset)}
							>
								Apply
							</button>
							<button
								type="button"
								class="btn"
								disabled={presets.busy !== null || cannotSave !== null}
								title={cannotSave ?? 'Replace this preset with the current changed values'}
								onclick={() => overwrite(preset)}
							>
								Overwrite
							</button>
							<button
								type="button"
								class="btn btn-ghost btn-icon"
								aria-label={`Rename ${preset.name}`}
								title="Rename"
								disabled={presets.busy !== null}
								onclick={() => startRename(preset)}
							>
								<Icon name="edit" size={18} />
							</button>
							<button
								type="button"
								class="btn btn-ghost btn-icon del"
								aria-label={`Delete ${preset.name}`}
								title="Delete"
								disabled={presets.busy !== null}
								onclick={() => remove(preset)}
							>
								<Icon name="trash" size={18} />
							</button>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</Sheet>

<style>
	.save {
		margin-bottom: var(--space-3);
	}
	label {
		font-weight: 600;
		font-size: var(--text-sm);
	}
	.hint {
		margin: 0;
		font-size: var(--text-xs);
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
	.item {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-2) var(--space-3);
	}
	.head {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.head strong {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.meta {
		font-size: var(--text-xs);
	}
	.del {
		margin-left: auto;
		color: var(--color-danger);
	}
</style>
