<script lang="ts">
	// Every control of the workflow, grouped by node, with search and a
	// placement filter. "Add" puts a control in the target section; on desktop a
	// row's grip can also be dragged onto the canvas.
	import type { ControlDescriptor } from '$lib/contracts';
	import Icon from '$lib/ui/Icon.svelte';
	import type { Designer } from './editor.svelte';
	import type { Dnd } from './dnd.svelte';

	let {
		editor,
		dnd,
		draggable = false
	}: { editor: Designer; dnd: Dnd; draggable?: boolean } = $props();

	type Filter = 'all' | 'unplaced' | 'placed' | 'hidden';
	const FILTERS: { id: Filter; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'unplaced', label: 'Unplaced' },
		{ id: 'placed', label: 'Placed' },
		{ id: 'hidden', label: 'Hidden' }
	];

	let query = $state('');
	let filter = $state<Filter>('all');

	function status(id: string): Exclude<Filter, 'all'> {
		const place = editor.placement.get(id);
		return !place ? 'unplaced' : place.kind === 'hidden' ? 'hidden' : 'placed';
	}

	function matches(control: ControlDescriptor, q: string): boolean {
		if (!q) return true;
		return [control.label, control.class_type, control.input_name, control.binding_id].some((text) =>
			text.toLowerCase().includes(q)
		);
	}

	const counts = $derived.by(() => {
		const out: Record<Filter, number> = { all: 0, unplaced: 0, placed: 0, hidden: 0 };
		for (const control of editor.schema?.controls ?? []) {
			out.all += 1;
			out[status(control.binding_id)] += 1;
		}
		return out;
	});

	const groups = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const byNode = new Map<string, { title: string; controls: ControlDescriptor[] }>();
		for (const control of editor.schema?.controls ?? []) {
			if (filter !== 'all' && status(control.binding_id) !== filter) continue;
			if (!matches(control, q)) continue;
			const group = byNode.get(control.node_id) ?? {
				title: `${control.class_type} #${control.node_id}`,
				controls: []
			};
			group.controls.push(control);
			byNode.set(control.node_id, group);
		}
		return [...byNode.entries()];
	});

	function where(id: string): string {
		const place = editor.placement.get(id);
		return !place ? 'Unplaced' : place.kind === 'hidden' ? 'Hidden' : place.title;
	}

	function select(id: string): void {
		editor.selectControl(id);
		void editor.reveal(id);
	}

	const target = $derived(
		editor.doc.sections.find((s) => s.id === editor.targetSectionId) ?? editor.doc.sections[0]
	);
</script>

<div class="library">
	<div class="search">
		<Icon name="search" size={16} />
		<input
			type="search"
			placeholder="Search controls"
			aria-label="Search controls"
			bind:value={query}
		/>
	</div>
	<div class="row wrap chips" role="group" aria-label="Filter controls">
		{#each FILTERS as f (f.id)}
			<button
				type="button"
				class="chip"
				aria-pressed={filter === f.id}
				onclick={() => (filter = f.id)}
			>
				{f.label} <span class="count">{counts[f.id]}</span>
			</button>
		{/each}
	</div>
	<p class="muted hint">
		Add goes to <strong>{target ? target.title : 'a new section'}</strong>.
	</p>

	{#each groups as [nodeId, group] (nodeId)}
		<section aria-label={group.title}>
			<h3>{group.title}</h3>
			<div class="row wrap">
				<button type="button" class="btn" disabled={editor.sectionsFull} onclick={() => editor.addNodeSection(nodeId, group.title)}>Add node as section</button>
				<button type="button" class="btn" onclick={() => editor.hideNode(nodeId)}>Hide all controls of this node</button>
			</div>
			<ul>
				{#each group.controls as control (control.binding_id)}
					{@const st = status(control.binding_id)}
					<li
						class:dragging={dnd.payload?.type === 'lib' && dnd.payload.id === control.binding_id}
					>
						{#if draggable}
							<span
								class="grip"
								title="Drag onto the canvas"
								aria-hidden="true"
								{@attach dnd.handle(() => ({
									type: 'lib',
									id: control.binding_id,
									label: control.label
								}))}
							>
								<Icon name="grip" size={18} />
							</span>
						{/if}
						<div class="info">
							<span class="name">{control.label}</span>
							<span class="where" class:placed={st === 'placed'}>
								{where(control.binding_id)} · {control.logical_type}
							</span>
						</div>
						{#if st === 'placed'}
							<button
								type="button"
								class="btn btn-ghost btn-icon"
								aria-label={`Select ${control.label} on the canvas`}
								title="Show on canvas"
								onclick={() => select(control.binding_id)}
							>
								<Icon name="chevron-right" size={18} />
							</button>
						{:else}
							<button
								type="button"
								class="btn btn-ghost btn-icon"
								aria-label={st === 'hidden' ? `Unhide ${control.label}` : `Hide ${control.label}`}
								title={st === 'hidden' ? 'Unhide (becomes unplaced)' : 'Hide'}
								disabled={st !== 'hidden' && !editor.canPlace(control.binding_id)}
								onclick={() =>
									st === 'hidden'
										? editor.unhideControl(control.binding_id)
										: editor.hideControl(control.binding_id)}
							>
								<Icon name={st === 'hidden' ? 'eye' : 'eye-off'} size={18} />
							</button>
							<button
								type="button"
								class="btn btn-icon"
								aria-label={`Add ${control.label}`}
								title={editor.canPlace(control.binding_id)
									? st === 'hidden'
										? 'Unhide and add'
										: 'Add to the target section'
									: editor.itemLimitHint}
								disabled={!editor.canPlace(control.binding_id)}
								onclick={() => editor.addControl(control.binding_id)}
							>
								<Icon name="plus" size={18} />
							</button>
						{/if}
					</li>
				{/each}
			</ul>
		</section>
	{:else}
		<p class="muted empty">No controls match.</p>
	{/each}
</div>

<style>
	.library {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-width: 0;
	}
	.search {
		position: relative;
		display: flex;
		align-items: center;
	}
	.search :global(svg) {
		position: absolute;
		left: 0.6rem;
		color: var(--color-text-faint);
		pointer-events: none;
	}
	.search input {
		padding-left: 2rem;
	}
	.chips {
		gap: var(--space-1);
	}
	.count {
		font-size: var(--text-xs);
		opacity: 0.7;
	}
	.hint {
		margin: 0;
		font-size: var(--text-xs);
	}
	h3 {
		margin: var(--space-2) 0 var(--space-1);
		font-size: var(--text-xs);
		font-weight: 650;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--color-text-faint);
		overflow-wrap: anywhere;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: 0.15rem 0.25rem 0.15rem 0.5rem;
		min-height: var(--touch-target);
		background: var(--color-surface-2);
		border: 1px solid transparent;
		border-radius: var(--radius);
	}
	li.dragging {
		opacity: 0.45;
	}
	.grip {
		flex: none;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.5rem;
		align-self: stretch;
		margin-left: -0.25rem;
		color: var(--color-text-faint);
		cursor: grab;
		touch-action: none;
		user-select: none;
	}
	.grip:hover {
		color: var(--color-text);
	}
	.info {
		flex: 1 1 auto;
		min-width: 0;
		display: flex;
		flex-direction: column;
		line-height: 1.25;
	}
	.name {
		font-size: var(--text-sm);
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.where {
		font-size: var(--text-xs);
		color: var(--color-text-faint);
	}
	.where.placed {
		color: var(--color-success);
	}
	.empty {
		padding: var(--space-3) 0;
		text-align: center;
	}
</style>
