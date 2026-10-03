<script lang="ts">
	// One layout section: a disclosure header and a grid of controls. Column
	// counts collapse by the width of the controls column (see .controls in
	// RunView): 1 on phones, at most 2 on tablets, the section's hint on desktop.
	import ControlRow from '$lib/controls/ControlRow.svelte';
	import AspectRatio from '$lib/controls/AspectRatio.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import type { RunState, ViewEntry, ViewSection } from './run.svelte';

	let { run, section }: { run: RunState; section: ViewSection } = $props();

	const on = $derived(!section.toggle || run.isOn(section.toggle));
	const open = $derived(run.isOpen(section));
	const modified = $derived(section.entries.flatMap((e) => e.height ? [e.control, e.height] : [e.control]).filter((c) => run.isModified(c)).length);
	const bodyId = $props.id();
</script>

{#snippet cell({ control, height, ratio, span }: ViewEntry)}
	<div class="cell" class:full={span === 'full'}>
		{#if ratio && height}
			<AspectRatio width={control} {height} presets={ratio.presets} widthValue={run.valueFor(control)} heightValue={run.valueFor(height)} onchange={(w, h) => { run.setValue(control, w); run.setValue(height, h); }} />
		{/if}
		<ControlRow
			{control}
			workflowId={run.workflowId}
			value={run.valueFor(control)}
			onchange={(value) => run.setValue(control, value)}
			modified={run.isModified(control)}
			onreset={() => run.reset(control)}
		/>
		{#if height}
			<ControlRow control={height} workflowId={run.workflowId} value={run.valueFor(height)} onchange={(value) => run.setValue(height, value)} modified={run.isModified(height)} onreset={() => run.reset(height)} />
		{/if}
	</div>
{/snippet}

<section class="card section" class:off={!on} id={`sec-${section.id}`}>
	<div class="headrow">
		<button
			type="button"
			class="head"
			aria-expanded={open}
			aria-controls={bodyId}
			onclick={() => run.toggle(section)}
		>
			<Icon name={open ? 'chevron-down' : 'chevron-right'} size={18} />
			<span class="title">{section.title}</span>
			{#if modified}<span class="badge badge-accent" title="Modified controls">{modified} changed</span>{/if}
			<span class="count muted">{section.entries.length}</span>
		</button>
		{#if section.toggle}
			{@const toggle = section.toggle}
			<input
				type="checkbox"
				class="switch"
				role="switch"
				aria-label={`${section.title}: ${toggle.label}`}
				title={toggle.label}
				checked={on}
				onchange={(e) => run.setValue(toggle, e.currentTarget.checked)}
			/>
		{/if}
	</div>
	{#if open && section.rows}
		<div class="rows" id={bodyId}>
			{#each section.rows as row (row.id)}
				<!-- Row → column → control: on phones the columns stack in that reading order. -->
				<div class="grid" data-cols={row.columns.length}>
					{#each row.columns as entries, ci (ci)}
						<div class="column">
							{#each entries as entry (entry.control.binding_id)}
								{@render cell(entry)}
							{/each}
						</div>
					{/each}
				</div>
			{/each}
		</div>
	{:else if open}
		<div class="grid" id={bodyId} data-cols={section.columns}>
			{#each section.entries as entry (entry.control.binding_id)}
				{@render cell(entry)}
			{/each}
		</div>
	{/if}
</section>

<style>
	.section {
		padding: 0;
		/* Room for the sticky jump chips when scrolled into view. */
		scroll-margin-top: calc(var(--toolbar-h, 3rem) + var(--space-2));
	}
	.headrow {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding-right: var(--space-3);
	}
	.headrow .switch {
		flex: none;
	}
	.section.off .title {
		color: var(--color-text-muted);
	}
	.head {
		flex: 1 1 auto;
		min-width: 0;
		display: flex;
		align-items: center;
		gap: var(--space-2);
		width: 100%;
		min-height: var(--touch-target);
		padding: 0 var(--space-3);
		font: inherit;
		color: var(--color-text);
		background: transparent;
		border: 0;
		border-radius: var(--radius-lg);
		cursor: pointer;
		text-align: left;
	}
	.title {
		font-weight: 650;
	}
	.count {
		margin-left: auto;
		font-size: var(--text-xs);
	}
	.badge {
		margin-left: var(--space-1);
	}
	.grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-3);
		padding: var(--space-1) var(--space-3) var(--space-3);
	}
	.cell {
		min-width: 0;
	}
	.rows {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		padding-bottom: var(--space-3);
	}
	.rows .grid {
		padding-bottom: 0;
	}
	.column {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		min-width: 0;
	}
	.cell.full {
		grid-column: 1 / -1;
	}
	@media (min-width: 768px) {
		@container run (min-width: 21rem) {
			.grid[data-cols='2'],
			.grid[data-cols='3'] {
				grid-template-columns: repeat(2, minmax(0, 1fr));
			}
		}
		@container run (min-width: 44rem) {
			.grid[data-cols='3'] {
				grid-template-columns: repeat(3, minmax(0, 1fr));
			}
		}
	}
</style>
