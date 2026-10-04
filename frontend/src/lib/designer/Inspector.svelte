<script lang="ts">
	// Properties of the selected control (presentation drafts + placement) or
	// section (title, columns, collapsed). Presentation edits are drafts: they
	// preview live and are only persisted by Save.
	import { ALLOWED_COMPONENTS, type Component } from '$lib/contracts';
	import { itemBindings, sectionItems } from '$lib/layout/model';
	import Icon from '$lib/ui/Icon.svelte';
	import type { Designer } from './editor.svelte';
	import { effectiveDraft, isRangedNumber } from './presentation';

	let { editor }: { editor: Designer } = $props();

	const selection = $derived(editor.selection);
	const control = $derived(
		selection?.kind === 'control' ? (editor.controlsById.get(selection.id) ?? null) : null
	);
	const section = $derived(
		selection?.kind === 'section'
			? (editor.doc.sections.find((s) => s.id === selection.id) ?? null)
			: null
	);
	const draft = $derived(
		control ? effectiveDraft(control, editor.drafts[control.binding_id]) : null
	);
	const ranged = $derived(control ? isRangedNumber(control) : false);
	const widgets = $derived.by((): Component[] => {
		if (!control) return [];
		const allowed = ALLOWED_COMPONENTS[control.logical_type];
		return allowed.includes(control.component) ? allowed : [control.component, ...allowed];
	});
	const place = $derived(control ? editor.placement.get(control.binding_id) : undefined);
	const placedItem = $derived.by(() => {
		if (!control || place?.kind !== 'section') return null;
		const s = editor.doc.sections.find((sec) => sec.id === place.id);
		return s
			? (sectionItems(s).find((i) => itemBindings(i).includes(control.binding_id)) ?? null)
			: null;
	});
	let ratioHeight = $state('');
	const booleans = $derived(
		(editor.schema?.controls ?? []).filter((c) => c.logical_type === 'boolean')
	);
	const counts = $derived({
		placed: [...editor.placement].filter(
			([id, p]) => p.kind === 'section' && editor.controlsById.has(id)
		).length,
		hidden: editor.doc.hidden.filter((id) => editor.controlsById.has(id)).length,
		total: editor.schema?.controls.length ?? 0
	});

	function rename(event: Event): void {
		if (!section) return;
		const input = event.currentTarget as HTMLInputElement;
		editor.updateSection(section.id, { title: input.value });
		input.value = editor.doc.sections.find((s) => s.id === section.id)?.title ?? '';
	}

	function chooseSection(event: Event): void {
		if (!control) return;
		const value = (event.currentTarget as HTMLSelectElement).value;
		if (value === '') editor.unplaceControl(control.binding_id);
		else editor.placeAt(control.binding_id, value);
	}
</script>

<div class="inspector stack">
	{#if control && draft}
		<div>
			<h2>{control.label}</h2>
			<p class="muted small mono">{control.binding_id}</p>
		</div>

		<div class="field">
			<label for="insp-label">Label</label>
			<input
				id="insp-label"
				value={draft.label}
				maxlength="200"
				oninput={(e) => editor.setDraft(control.binding_id, { label: e.currentTarget.value })}
			/>
		</div>

		<div class="field">
			<label for="insp-widget">Widget</label>
			<select
				id="insp-widget"
				value={draft.component}
				disabled={widgets.length < 2}
				onchange={(e) =>
					editor.setDraft(control.binding_id, { component: e.currentTarget.value as Component })}
			>
				{#each widgets as component (component)}
					<option value={component}>{component}</option>
				{/each}
			</select>
		</div>

		{#if ranged}
			<fieldset class="range">
				<legend>Display range</legend>
				<div class="range-grid">
					{#each [['display_min', 'Min'], ['display_max', 'Max'], ['display_step', 'Step']] as const as [key, name] (key)}
						<div class="field">
							<label for={`insp-${key}`}>{name}</label>
							<input
								id={`insp-${key}`}
								type="number"
								step="any"
								value={draft[key]}
								oninput={(e) =>
									editor.setDraft(control.binding_id, { [key]: e.currentTarget.value })}
							/>
						</div>
					{/each}
				</div>
				<p class="muted small">A display range can only narrow the declared limits.</p>
			</fieldset>
		{/if}

		<div class="field">
			<label for="insp-help">Help text</label>
			<textarea
				id="insp-help"
				rows="2"
				maxlength="2000"
				value={draft.help_text}
				oninput={(e) => editor.setDraft(control.binding_id, { help_text: e.currentTarget.value })}
			></textarea>
		</div>

		{#if control.logical_type === 'int' && placedItem?.kind === 'control'}
			<div class="field">
				<label for="ratio-height">Pair as width with height control</label>
				<select id="ratio-height" bind:value={ratioHeight}>
					<option value="">Choose height…</option>
					{#each editor.schema?.controls.filter((c) => c.logical_type === 'int' && c.binding_id !== control.binding_id && editor.doc.sections.some( (s) => sectionItems(s).some((i) => i.kind === 'control' && i.binding_id === c.binding_id) )) ?? [] as candidate (candidate.binding_id)}
						<option value={candidate.binding_id}>{candidate.label} ({candidate.binding_id})</option>
					{/each}
				</select>
				<button
					type="button"
					class="btn"
					disabled={!ratioHeight}
					onclick={() => {
						editor.pair(control.binding_id, ratioHeight);
						ratioHeight = '';
					}}>Create aspect-ratio control</button
				>
			</div>
		{:else if placedItem?.kind === 'aspect_ratio'}
			<p class="muted small">Width: {placedItem.width} · Height: {placedItem.height}</p>
			<button type="button" class="btn" onclick={() => editor.split(control.binding_id)}
				>Split dimension controls</button
			>
		{/if}

		<hr />

		<div class="field">
			<label for="insp-section">Section</label>
			<select
				id="insp-section"
				value={place?.kind === 'section' ? place.id : ''}
				onchange={chooseSection}
			>
				<option value="">{place?.kind === 'hidden' ? 'Hidden' : 'Not placed (More)'}</option>
				{#each editor.doc.sections as s (s.id)}
					<option value={s.id}>{s.title}</option>
				{/each}
			</select>
		</div>

		{#if placedItem && editor.doc.sections.find((s) => s.id === (place?.kind === 'section' ? place.id : ''))?.mode !== 'panels'}
			<div class="field">
				<span class="label" id="insp-span">Width</span>
				<div class="seg" role="group" aria-labelledby="insp-span">
					<button
						type="button"
						aria-pressed={(placedItem.span ?? 'auto') === 'auto'}
						onclick={() => editor.setSpan(control.binding_id, 'auto')}
					>
						One cell
					</button>
					<button
						type="button"
						aria-pressed={placedItem.span === 'full'}
						onclick={() => editor.setSpan(control.binding_id, 'full')}
					>
						Full row
					</button>
				</div>
			</div>
		{/if}

		<div class="field">
			<span class="label">Default value</span>
			{#if control.binding_id in editor.previewValues}
				<p class="small">
					Changed from <code>{String(control.value ?? '')}</code>; saved as the workflow's new
					default.
				</p>
				<button type="button" class="btn" onclick={() => editor.revertValue(control.binding_id)}>
					<Icon name="reset" size={16} /> Revert value
				</button>
			{:else}
				<p class="muted small">
					Change the control on the canvas to set its default for every run.
				</p>
			{/if}
		</div>

		{#if placedItem}
			<div class="field">
				<label for="insp-when">Show only when</label>
				<select
					id="insp-when"
					value={placedItem.when ?? ''}
					onchange={(e) => editor.setWhen(control.binding_id, e.currentTarget.value || null)}
				>
					<option value="">Always shown</option>
					{#each booleans as b (b.binding_id)}
						{#if b.binding_id !== control.binding_id}<option value={b.binding_id}
								>{editor.preview(b).label} is on</option
							>{/if}
					{/each}
					{#if placedItem.when && !booleans.some((b) => b.binding_id === placedItem.when)}
						<option value={placedItem.when}>{placedItem.when} (missing, always shown)</option>
					{/if}
				</select>
			</div>
		{/if}

		<div class="row wrap">
			{#if place?.kind === 'hidden'}
				<button type="button" class="btn" onclick={() => editor.unhideControl(control.binding_id)}>
					<Icon name="eye" size={16} /> Unhide
				</button>
			{:else}
				<button
					type="button"
					class="btn"
					disabled={!editor.canPlace(control.binding_id)}
					title={editor.canPlace(control.binding_id) ? undefined : editor.itemLimitHint}
					onclick={() => editor.hideControl(control.binding_id)}
				>
					<Icon name="eye-off" size={16} /> Hide
				</button>
			{/if}
			<button
				type="button"
				class="btn"
				disabled={!editor.hasPresentation(control.binding_id) || editor.busy}
				onclick={() => editor.resetPresentation(control.binding_id)}
			>
				<Icon name="reset" size={16} /> Reset presentation
			</button>
		</div>

		<details class="prov">
			<summary>Provenance</summary>
			<dl>
				<dt>Node class</dt>
				<dd>{control.class_type}</dd>
				<dt>Node id</dt>
				<dd>{control.node_id}</dd>
				<dt>Input</dt>
				<dd>{control.input_name}</dd>
				<dt>Type</dt>
				<dd>{control.logical_type}</dd>
				<dt>Why this widget</dt>
				<dd>{control.inference_reason}</dd>
				<dt>Imported value</dt>
				<dd class="value">
					{control.value === null || control.value === '' ? '(none)' : String(control.value)}
				</dd>
			</dl>
		</details>
	{:else if section}
		<div>
			<h2>Section</h2>
			<p class="muted small">{sectionItems(section).length} controls</p>
		</div>
		<div class="field">
			<label for="insp-title">Title</label>
			<input id="insp-title" value={section.title} maxlength="80" onchange={rename} />
		</div>
		<div class="field">
			<span class="label" id="insp-mode">Layout</span>
			<div class="seg" role="group" aria-labelledby="insp-mode">
				<button
					type="button"
					aria-pressed={section.mode !== 'panels'}
					onclick={() => editor.setMode(section.id, 'auto')}>Automatic</button
				>
				<button
					type="button"
					aria-pressed={section.mode === 'panels'}
					onclick={() => editor.setMode(section.id, 'panels')}>Rows &amp; columns</button
				>
			</div>
		</div>
		{#if section.mode === 'panels'}
			<div class="field">
				<p class="muted small">
					{section.rows.length} row{section.rows.length === 1 ? '' : 's'}. Edit rows and columns on
					the canvas. Phones stack columns, tablets show at most two.
				</p>
				<button type="button" class="btn" onclick={() => editor.addRow(section.id)}>Add row</button>
			</div>
		{:else}
			<div class="field">
				<span class="label" id="insp-cols">Columns</span>
				<div class="seg" role="group" aria-labelledby="insp-cols">
					{#each [1, 2, 3] as n (n)}
						<button
							type="button"
							aria-pressed={section.columns === n}
							onclick={() => editor.updateSection(section.id, { columns: n as 1 | 2 | 3 })}
						>
							{n}
						</button>
					{/each}
				</div>
				<p class="muted small">
					A desktop hint. Phones always use one column, tablets at most two.
				</p>
			</div>
		{/if}
		<div class="field">
			<label for="insp-toggle">On/off switch</label>
			<select
				id="insp-toggle"
				value={section.toggle ?? ''}
				onchange={(e) => editor.setToggle(section.id, e.currentTarget.value || null)}
			>
				<option value="">None</option>
				{#each booleans as b (b.binding_id)}
					<option value={b.binding_id}>{editor.preview(b).label} ({b.binding_id})</option>
				{/each}
				{#if section.toggle && !booleans.some((b) => b.binding_id === section.toggle)}
					<option value={section.toggle}>{section.toggle} (missing)</option>
				{/if}
			</select>
			<p class="muted small">
				Shown in the section header on the run page; the section's controls appear only while it is
				on.
			</p>
		</div>
		<label class="check">
			<input
				type="checkbox"
				class="switch"
				role="switch"
				checked={section.collapsed}
				onchange={(e) => editor.updateSection(section.id, { collapsed: e.currentTarget.checked })}
			/>
			<span>Collapsed by default</span>
		</label>
		<button
			type="button"
			class="btn btn-danger"
			onclick={() => section && editor.confirmDeleteSection(section.id)}
		>
			<Icon name="trash" size={16} /> Delete section
		</button>
	{:else}
		<div>
			<h2>Inspector</h2>
			<p class="muted">Select a control or a section on the canvas to edit it.</p>
		</div>
		<dl class="stats">
			<dt>Controls</dt>
			<dd>{counts.total}</dd>
			<dt>Placed</dt>
			<dd>{counts.placed}</dd>
			<dt>Hidden</dt>
			<dd>{counts.hidden}</dd>
			<dt>Unplaced</dt>
			<dd>{counts.total - counts.placed - counts.hidden}</dd>
		</dl>
		<p class="muted small">
			Unplaced controls appear in a collapsed "More" section on the run page. Hidden ones never
			show, but keep their imported values.
		</p>
	{/if}
</div>

<style>
	.inspector {
		--gap: var(--space-3);
		min-width: 0;
	}
	h2 {
		margin: 0;
		overflow-wrap: anywhere;
	}
	.small {
		margin: 0;
		font-size: var(--text-xs);
	}
	.mono {
		font-family: var(--font-mono);
		overflow-wrap: anywhere;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		min-width: 0;
	}
	.field label,
	.label,
	legend {
		font-size: var(--text-sm);
		font-weight: 600;
		color: var(--color-text-muted);
	}
	fieldset {
		margin: 0;
		padding: 0;
		border: 0;
		min-width: 0;
	}
	.range-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: var(--space-2);
		margin: var(--space-1) 0;
	}
	.seg {
		display: inline-flex;
		align-self: flex-start;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		overflow: hidden;
	}
	.seg button {
		min-height: var(--control-h);
		padding: 0 0.875rem;
		font-weight: 600;
		color: var(--color-text-muted);
		background: var(--color-surface-2);
		border: 0;
		border-right: 1px solid var(--color-border);
		cursor: pointer;
	}
	.seg button:last-child {
		border-right: 0;
	}
	.seg button[aria-pressed='true'] {
		color: var(--color-accent-text);
		background: var(--color-accent);
	}
	.check {
		display: inline-flex;
		align-items: center;
		gap: 0.6rem;
		min-height: var(--control-h);
		cursor: pointer;
	}
	hr {
		margin: 0;
	}
	.prov summary {
		cursor: pointer;
		font-size: var(--text-sm);
		font-weight: 600;
		color: var(--color-text-muted);
	}
	dl {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		gap: 0.2rem var(--space-3);
		margin: var(--space-2) 0 0;
		font-size: var(--text-sm);
	}
	dt {
		color: var(--color-text-faint);
	}
	dd {
		margin: 0;
		overflow-wrap: anywhere;
	}
	.value {
		font-family: var(--font-mono);
	}
	.stats {
		margin: 0;
	}
</style>
