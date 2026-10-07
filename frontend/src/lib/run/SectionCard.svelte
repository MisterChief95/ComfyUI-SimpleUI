<script lang="ts">
	// One layout section: a disclosure header and a grid of controls. Column
	// counts collapse by the width of the controls column (see .controls in
	// RunView): 1 on phones, at most 2 on tablets, the section's hint on desktop.
	import LayoutControls from '$lib/controls/LayoutControls.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import { slide } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import type { RunState, ViewEntry, ViewSection } from './run.svelte';

	let { run, section }: { run: RunState; section: ViewSection } = $props();

	const on = $derived(!section.toggle || run.isOn(section.toggle));
	const open = $derived(run.isOpen(section));
	const modified = $derived(
		section.entries
			.flatMap((e) => (e.height ? [e.control, e.height] : [e.control]))
			.filter((c) => run.isModified(c)).length
	);
	const bodyId = $props.id();
</script>

{#snippet cell({ control, height, ratio, span }: ViewEntry)}
	<div class="cell" class:full={span === 'full'}>
		<LayoutControls
			{control}
			{height}
			{ratio}
			workflowId={run.workflowId}
			valueFor={(entry) => run.valueFor(entry)}
			onchange={(entry, value) => run.setValue(entry, value)}
			isModified={(entry) => run.isModified(entry)}
			onreset={(entry) => run.reset(entry)}
			lastSeed={(entry) => run.lastSeed(entry)}
		/>
	</div>
{/snippet}

<section class="card section" class:off={!on} class:expanded={open} id={`sec-${section.id}`}>
	<div class="headrow">
		<button
			type="button"
			class="head"
			aria-expanded={open}
			aria-controls={bodyId}
			onclick={() => run.toggle(section)}
		>
			<span class="chevron" class:open><Icon name="chevron-right" size={18} /></span>
			<span class="title">{section.title}</span>
			{#if modified}<span class="badge badge-accent" title="Modified controls"
					>{modified} changed</span
				>{/if}
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
		<div
			class="rows"
			id={bodyId}
			transition:slide={{ duration: prefersReducedMotion.current ? 0 : 180, easing: cubicOut }}
		>
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
		<div
			class="grid"
			id={bodyId}
			data-cols={section.columns}
			transition:slide={{ duration: prefersReducedMotion.current ? 0 : 180, easing: cubicOut }}
		>
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
	.expanded .headrow {
		border-bottom: 1px solid var(--color-border);
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
		letter-spacing: -0.015em;
	}
	.chevron {
		display: flex;
		transition: rotate 180ms ease-out;
	}
	.chevron.open {
		rotate: 90deg;
	}
	.head:hover {
		background: var(--color-surface-2);
	}
	.count {
		margin-left: auto;
		font-size: var(--text-xs);
		font-variant-numeric: tabular-nums;
		padding: 0.15rem 0.45rem;
		background: var(--color-surface-2);
		border-radius: var(--radius-sm);
	}
	.badge {
		margin-left: var(--space-1);
	}
	.grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-3);
		padding: var(--space-2) var(--space-3) var(--space-3);
	}
	.cell {
		min-width: 0;
	}
	.rows {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		padding-block: var(--space-2) var(--space-3);
	}
	.rows .grid {
		padding-top: 0;
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
	/* Phone and tablet: flat, edge-to-edge accordions instead of cards. The
	   header bar sticks under the toolbar while its section scrolls past. */
	@media (max-width: 1023px) {
		.section {
			background: var(--color-surface-1);
			border: 0;
			border-bottom: 1px solid var(--color-border);
			border-radius: 0;
			scroll-margin-top: var(--toolbar-h, 3rem);
		}
		.headrow {
			position: sticky;
			top: var(--toolbar-h, 0px);
			z-index: 1;
			padding-right: max(var(--page-pad), env(safe-area-inset-right));
			background: var(--color-surface-2);
		}
		.expanded .headrow {
			border-bottom-color: var(--color-border);
		}
		.head {
			padding-inline: var(--page-pad);
			border-radius: 0;
		}
		.head:hover {
			background: var(--color-surface-3);
		}
		.count {
			background: var(--color-surface-3);
		}
		.grid {
			padding: var(--space-2) var(--page-pad) var(--space-3);
		}
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
