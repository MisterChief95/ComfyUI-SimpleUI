<script lang="ts">
	import { onDestroy } from 'svelte';
	import type { ControlDescriptor, Group } from '$lib/contracts';
	import { GenerationFormState } from './generation.svelte';
	import ControlField from './ControlField.svelte';

	let { workflowId }: { workflowId: string } = $props();

	const form = new GenerationFormState(workflowId);
	form.load();
	onDestroy(() => form.dispose());

	// display_min/display_max is presentation only; group order matches the
	// backend's deterministic GROUP_ORDER (app/mapping/controls.py) closely
	// enough for a compact layout without duplicating that table here.
	const GROUP_ORDER: Group[] = [
		'prompts',
		'model',
		'generation',
		'dimensions',
		'inputs',
		'video',
		'advanced',
		'output',
		'inactive'
	];
	const COLLAPSED_BY_DEFAULT: Group[] = ['advanced', 'inactive'];

	let expanded = $state<Set<Group>>(new Set());

	function toggle(group: Group): void {
		const next = new Set(expanded);
		if (next.has(group)) next.delete(group);
		else next.add(group);
		expanded = next;
	}

	function grouped(controls: ControlDescriptor[]): Map<Group, ControlDescriptor[]> {
		const byGroup = new Map<Group, ControlDescriptor[]>();
		for (const control of controls) {
			const list = byGroup.get(control.group) ?? [];
			list.push(control);
			byGroup.set(control.group, list);
		}
		for (const list of byGroup.values()) list.sort((a, b) => a.order - b.order);
		return byGroup;
	}

	function displayValue(control: ControlDescriptor): string {
		if (control.binding_id in form.edits) return form.edits[control.binding_id];
		if (control.value === null || control.value === undefined) return '';
		return String(control.value);
	}
</script>

{#if form.loading}
	<p class="status">Loading workflow…</p>
{:else if form.loadError}
	<p class="status error" role="alert">{form.loadError}</p>
{:else if form.schema}
	{@const schema = form.schema}
	{#if schema.blocking.length > 0}
		<div class="blocking" role="alert">
			<h2>This workflow cannot be submitted yet</h2>
			{#each schema.blocking as detail (detail.code + (detail.field ?? ''))}
				<p>{detail.message}</p>
			{/each}
		</div>
	{/if}
	{#if schema.warnings.length > 0}
		<details class="warnings">
			<summary>{schema.warnings.length} mapping warning(s)</summary>
			{#each schema.warnings as detail (detail.code + (detail.field ?? ''))}
				<p>{detail.message}</p>
			{/each}
		</details>
	{/if}

	{#each GROUP_ORDER as group (group)}
		{@const controls = grouped(schema.controls).get(group)}
		{#if controls && controls.length > 0}
			{@const collapsedDefault = COLLAPSED_BY_DEFAULT.includes(group)}
			{@const isOpen = expanded.has(group) !== collapsedDefault}
			<section class="group">
				<button type="button" class="group-header" onclick={() => toggle(group)}>
					<span>{group}</span>
					<span aria-hidden="true">{isOpen ? '▾' : '▸'}</span>
				</button>
				{#if isOpen}
					<div class="group-body">
						{#each controls as control (control.binding_id)}
							<ControlField
								{control}
								value={displayValue(control)}
								{workflowId}
								onInput={(id, value) => form.setEdit(id, value)}
							/>
						{/each}
					</div>
				{/if}
			</section>
		{/if}
	{/each}

	<div class="seed-policy">
		<label for="seed-policy">Seed policy</label>
		<select id="seed-policy" bind:value={form.seedPolicy}>
			<option value="fixed">Fixed</option>
			<option value="random">Random</option>
			<option value="increment">Increment</option>
		</select>
	</div>

	<div class="submit-row">
		<button
			type="button"
			class="generate"
			onclick={() => form.submit()}
			disabled={form.submitting || schema.blocking.length > 0}
		>
			{form.submitting ? 'Submitting…' : 'Generate'}
		</button>
		{#if form.submitError}<p class="status error" role="alert">{form.submitError}</p>{/if}
	</div>

	{#if form.latest}
		<section class="result">
			<h2>Latest result</h2>
			<p>Status: <strong>{form.latest.status}</strong></p>
			<p>Output: {form.latest.output_state}</p>
			{#if form.latest.error}
				<pre class="error">{JSON.stringify(form.latest.error, null, 2)}</pre>
			{/if}
		</section>
	{/if}
{/if}

<style>
	.status {
		padding: var(--space-3) 0;
	}

	.status.error,
	.error {
		color: var(--color-danger);
	}

	.blocking {
		padding: var(--space-3);
		margin-bottom: var(--space-3);
		border: 1px solid var(--color-danger);
		border-radius: var(--radius);
	}

	.blocking h2 {
		margin: 0 0 var(--space-2);
		font-size: 1rem;
	}

	.warnings {
		margin-bottom: var(--space-3);
		font-size: 0.875rem;
		color: var(--color-text-muted);
	}

	.group {
		margin-bottom: var(--space-2);
	}

	.group-header {
		display: flex;
		width: 100%;
		align-items: center;
		justify-content: space-between;
		min-height: var(--touch-target);
		padding: 0 var(--space-2);
		background: var(--color-bg-elevated);
		border: none;
		border-radius: var(--radius);
		font-weight: 600;
		text-transform: capitalize;
		cursor: pointer;
	}

	.group-body {
		padding: 0 var(--space-2);
	}

	.seed-policy {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: var(--space-3) 0;
	}

	.seed-policy select {
		min-height: var(--touch-target);
		padding: 0 var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-bg);
	}

	/* A sticky bar that never covers the last field on a phone keyboard: it
	   sits in normal flow, not fixed/absolute. */
	.submit-row {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-3) 0;
	}

	.generate {
		min-height: var(--touch-target);
		padding: 0 var(--space-4);
		border: none;
		border-radius: var(--radius);
		background: var(--color-accent);
		color: var(--color-accent-text);
		font-weight: 600;
		cursor: pointer;
	}

	.generate:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.result {
		padding: var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
	}

	.result pre {
		white-space: pre-wrap;
		word-break: break-word;
	}
</style>
