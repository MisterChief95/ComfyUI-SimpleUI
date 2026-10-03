<script lang="ts">
	// The layout being designed: section cards in the chosen preview width, each
	// with live widgets; changing a value drafts a new workflow default (saved by Save). Drag grips, tap buttons and the
	// inspector all edit the same Designer state.
	import AspectRatio from '$lib/controls/AspectRatio.svelte';
	import {
		itemBindings,
		itemId,
		sectionItems,
		MAX_ROWS,
		type ResolvedControl
	} from '$lib/layout/model';
	import ControlRow from '$lib/controls/ControlRow.svelte';
	import type { LayoutSection } from '$lib/contracts';
	import Icon from '$lib/ui/Icon.svelte';
	import { tick } from 'svelte';
	import { PREVIEW_PX, type Designer } from './editor.svelte';
	import type { Dnd } from './dnd.svelte';

	let {
		editor,
		dnd,
		compact = false,
		onedit
	}: {
		editor: Designer;
		dnd: Dnd;
		/** Phone: the selected control shows an "Edit" button that opens the inspector sheet. */
		compact?: boolean;
		onedit?: () => void;
	} = $props();

	const resolved = $derived(editor.resolved);
	const px = $derived(PREVIEW_PX[editor.previewWidth]);

	// Column hints collapse like the run page: one column when narrow, at most two on a tablet.
	let frameWidth = $state(0);
	const cap = $derived(frameWidth < 560 ? 1 : frameWidth < 900 ? 2 : 3);

	function selectedControl(id: string): boolean {
		return editor.selection?.kind === 'control' && editor.selection.id === id;
	}
	function selectedSection(id: string): boolean {
		return editor.selection?.kind === 'section' && editor.selection.id === id;
	}

	function rename(event: Event, section: LayoutSection): void {
		const input = event.currentTarget as HTMLInputElement;
		editor.updateSection(section.id, { title: input.value });
		input.value = editor.doc.sections.find((s) => s.id === section.id)?.title ?? section.title;
	}

	function removeSection(section: LayoutSection): void {
		if (
			sectionItems(section).length > 0 &&
			!confirm(
				`Delete "${section.title}"? Its ${sectionItems(section).length} controls become unplaced.`
			)
		) {
			return;
		}
		editor.deleteSection(section.id);
	}

	function setColumns(section: LayoutSection, n: 1 | 2 | 3): void {
		if (section.mode === 'panels') editor.setMode(section.id, 'auto');
		editor.updateSection(section.id, { columns: n });
	}

	function removeRow(
		section: LayoutSection,
		row: { id: string; columns: { controls: unknown[] }[] }
	): void {
		const n = row.columns.reduce((sum, c) => sum + c.controls.length, 0);
		if (n > 0 && !confirm(`Delete this row? Its ${n} controls become unplaced.`)) return;
		editor.removeRow(section.id, row.id);
	}

	function removeColumn(
		section: LayoutSection,
		rowId: string,
		column: { id: string; controls: unknown[] }
	): void {
		const n = column.controls.length;
		if (n > 0 && !confirm(`Delete this column? Its ${n} controls become unplaced.`)) return;
		editor.removeColumn(section.id, rowId, column.id);
	}

	async function insertSection(index: number): Promise<void> {
		const id = editor.createSection('New section', index);
		if (!id) return;
		await tick();
		document.querySelector<HTMLInputElement>(`[data-section-card="${id}"] .title`)?.focus();
	}

	const dropSection = $derived(dnd.target?.type === 'items' ? dnd.target.sectionId : null);
</script>

{#snippet card(rc: ResolvedControl, section: LayoutSection)}
	{@const id = itemId(rc.item)}
	{@const control = editor.preview(rc.control)}
	{@const picked = selectedControl(id)}
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
	<article
		class="item"
		class:full={rc.item.span === 'full'}
		class:selected={picked}
		class:dragging={dnd.payload?.type !== 'section' && dnd.payload?.id === id}
		data-item={id}
		data-pair-height={rc.item.kind === 'aspect_ratio' ? rc.item.height : undefined}
		onclick={() => editor.selectControl(id)}
	>
		<div class="bar">
			<span
				class="grip"
				title="Drag to move"
				aria-hidden="true"
				{@attach dnd.handle(() => ({ type: 'item', id, label: control.label }))}
			>
				<Icon name="grip" size={18} />
			</span>
			<button
				type="button"
				class="pick"
				aria-pressed={picked}
				aria-label={`Select ${control.label}`}
				onfocus={() => editor.selectControl(id)}
			>
				<code>{id}</code>
			</button>
			{#if id in editor.drafts}<span class="badge badge-accent">edited</span>{/if}
			{#if itemBindings(rc.item).some((b) => b in editor.previewValues)}<span
					class="badge badge-accent"
					title="Unsaved default value">default changed</span
				>{/if}
			{#if rc.item.span === 'full'}<span class="badge">full row</span>{/if}
			{#if rc.when}<span class="badge" title="Shown only while this is on"
					>when {editor.preview(rc.when).label}</span
				>{:else if rc.item.when}<span
					class="badge badge-warning"
					title="This condition is missing or not a boolean, so the control always shows"
					>condition missing</span
				>{/if}
		</div>
		{#if rc.item.kind === 'aspect_ratio' && rc.height}
			<AspectRatio
				width={control}
				height={editor.preview(rc.height)}
				presets={rc.item.presets}
				widthValue={editor.previewValue(control)}
				heightValue={editor.previewValue(rc.height)}
				onchange={(w, h) => {
					editor.setPreviewValue(id, w);
					editor.setPreviewValue(rc.height!.binding_id, h);
				}}
			/>
		{/if}
		<ControlRow
			{control}
			value={editor.previewValue(control)}
			onchange={(value) => editor.setPreviewValue(id, value)}
			compact
			preview
		/>
		{#if rc.height}
			<ControlRow
				control={editor.preview(rc.height)}
				value={editor.previewValue(rc.height)}
				onchange={(value) => editor.setPreviewValue(rc.height!.binding_id, value)}
				compact
				preview
			/>
		{/if}
		{#if picked}
			<div class="actions" role="group" aria-label={`Actions for ${control.label}`}>
				<button
					type="button"
					class="btn btn-icon"
					aria-label="Move up"
					title="Move up"
					onclick={() => editor.shiftItem(id, -1)}
				>
					<Icon name="chevron-up" size={18} />
				</button>
				<button
					type="button"
					class="btn btn-icon"
					aria-label="Move down"
					title="Move down"
					onclick={() => editor.shiftItem(id, 1)}
				>
					<Icon name="chevron-down" size={18} />
				</button>
				<select
					class="moveto"
					aria-label="Move to section"
					value={section.id}
					onchange={(e) => editor.placeAt(id, e.currentTarget.value)}
				>
					<option value={section.id} disabled>Move to…</option>
					{#each editor.doc.sections as s (s.id)}
						{#if s.id !== section.id}<option value={s.id}>{s.title}</option>{/if}
					{/each}
				</select>
				{#if section.mode === 'panels'}
					<button
						type="button"
						class="btn btn-icon"
						aria-label="Move to previous column"
						title="Previous column"
						disabled={!editor.canShiftColumn(id, -1)}
						onclick={() => editor.shiftColumn(id, -1)}
					>
						<Icon name="chevron-left" size={18} />
					</button>
					<button
						type="button"
						class="btn btn-icon"
						aria-label="Move to next column"
						title="Next column"
						disabled={!editor.canShiftColumn(id, 1)}
						onclick={() => editor.shiftColumn(id, 1)}
					>
						<Icon name="chevron-right" size={18} />
					</button>
				{/if}
				{#if section.mode !== 'panels'}
					<button
						type="button"
						class="btn"
						aria-pressed={rc.item.span === 'full'}
						onclick={() => editor.setSpan(id, rc.item.span === 'full' ? 'auto' : 'full')}
					>
						{rc.item.span === 'full' ? 'One cell' : 'Full row'}
					</button>
				{/if}
				<button
					type="button"
					class="btn"
					disabled={!editor.canPlace(id)}
					title={editor.canPlace(id) ? undefined : editor.itemLimitHint}
					onclick={() => editor.hideControl(id)}
				>
					<Icon name="eye-off" size={16} /> Hide
				</button>
				{#if compact}
					<button type="button" class="btn btn-primary" onclick={() => onedit?.()}>Edit</button>
				{/if}
			</div>
		{/if}
	</article>
{/snippet}

<div class="canvas" class:compact data-autoscroll>
	{#if resolved && resolved.stale.length > 0}
		<div class="banner card" role="status">
			<p class="banner-title"><Icon name="alert" size={16} /> Stale controls in this layout</p>
			<p class="muted small">
				These bindings no longer exist in the workflow. They are kept until you remove them.
			</p>
			<ul>
				{#each resolved.stale as id (id)}
					<li>
						<code>{id}</code>
						<button
							type="button"
							class="btn btn-danger"
							aria-label={`Remove stale ${id}`}
							onclick={() => editor.removeStaleBinding(id)}
						>
							Remove
						</button>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	<div
		class="frame"
		class:constrained={px !== null}
		style:max-width={px === null ? undefined : `${px}px`}
		bind:clientWidth={frameWidth}
		data-section-list
	>
		{#if editor.fresh}
			<div class="empty card stack">
				<h2>Design your run page</h2>
				<p class="muted">
					This workflow has no saved layout yet, so the run page shows the automatic one. Start from
					that, or build your own from the library.
				</p>
				<div class="row wrap">
					<button type="button" class="btn btn-primary" onclick={() => editor.startFromAutomatic()}>
						Start from automatic layout
					</button>
					<button type="button" class="btn" onclick={() => editor.startEmpty()}>Start empty</button>
				</div>
			</div>
		{:else if resolved}
			{#each resolved.sections as rs, si (rs.section.id)}
				{@const section = rs.section}
				{#if !editor.sectionsFull && dnd.payload?.type !== 'section'}
					<div class="insert">
						<button
							type="button"
							aria-label={`Insert section before ${section.title}`}
							title="Insert section here"
							onclick={() => insertSection(si)}
						>
							<Icon name="plus" size={14} /><span>Section</span>
						</button>
					</div>
				{/if}
				<section
					class="sec"
					class:selected={selectedSection(section.id)}
					class:dragging={dnd.payload?.type === 'section' && dnd.payload.id === section.id}
					class:target={dropSection === section.id}
					data-section-card={section.id}
					aria-label={section.title}
				>
					<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
					<header onclick={() => editor.selectSection(section.id)}>
						<span
							class="grip"
							title="Drag to reorder this section"
							aria-hidden="true"
							{@attach dnd.handle(() => ({
								type: 'section',
								id: section.id,
								label: section.title
							}))}
						>
							<Icon name="grip" size={20} />
						</span>
						<input
							class="title"
							value={section.title}
							maxlength="80"
							aria-label="Section title"
							onfocus={() => editor.selectSection(section.id)}
							onchange={(e) => rename(e, section)}
							onkeydown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
						/>
						<div class="seg" role="group" aria-label="Columns">
							{#each [1, 2, 3] as n (n)}
								<button
									type="button"
									aria-pressed={section.mode !== 'panels' && section.columns === n}
									aria-label={`${n} column${n > 1 ? 's' : ''}, automatic flow`}
									onclick={() => setColumns(section, n as 1 | 2 | 3)}
								>
									{n}
								</button>
							{/each}
							<button
								type="button"
								aria-pressed={section.mode === 'panels'}
								aria-label="Rows and columns"
								title="Rows and columns: arrange controls in panels"
								onclick={() => editor.setMode(section.id, 'panels')}
							>
								<Icon name="grid" size={16} />
							</button>
						</div>
						{#if rs.toggle}
							<span class="badge" title="On/off switch shown in this section's header"
								>switch: {editor.preview(rs.toggle).label}</span
							>
						{:else if section.toggle}
							<span
								class="badge badge-warning"
								title="The switch control is missing or not a boolean">switch missing</span
							>
						{/if}
						<label class="collapse" title="Start collapsed on the run page">
							<input
								type="checkbox"
								class="switch"
								role="switch"
								checked={section.collapsed}
								onchange={(e) =>
									editor.updateSection(section.id, { collapsed: e.currentTarget.checked })}
							/>
							<span>Collapsed</span>
						</label>
						<span class="spacer"></span>
						<button
							type="button"
							class="btn btn-ghost btn-icon"
							aria-label={`Move ${section.title} up`}
							disabled={si === 0}
							onclick={() => editor.shiftSection(section.id, -1)}
						>
							<Icon name="chevron-up" size={18} />
						</button>
						<button
							type="button"
							class="btn btn-ghost btn-icon"
							aria-label={`Move ${section.title} down`}
							disabled={si === resolved.sections.length - 1}
							onclick={() => editor.shiftSection(section.id, 1)}
						>
							<Icon name="chevron-down" size={18} />
						</button>
						<button
							type="button"
							class="btn btn-ghost btn-icon danger"
							aria-label={`Delete ${section.title}`}
							onclick={() => removeSection(section)}
						>
							<Icon name="trash" size={18} />
						</button>
					</header>

					{#if section.mode === 'panels' && rs.rows}
						<div class="panels">
							{#each rs.rows as row, ri (row.id)}
								<div class="prow" role="group" aria-label={`Row ${ri + 1}`}>
									<div class="rowbar">
										<span class="muted small">Row {ri + 1}</span>
										<div class="seg" role="group" aria-label={`Row ${ri + 1} columns`}>
											{#each [1, 2, 3] as n (n)}
												<button
													type="button"
													aria-pressed={row.columns.length === n}
													aria-label={`${n} column${n > 1 ? 's' : ''}`}
													onclick={() => editor.setRowColumns(section.id, row.id, n)}
												>
													{n}
												</button>
											{/each}
										</div>
										<span class="spacer"></span>
										<button
											type="button"
											class="btn btn-ghost btn-icon"
											aria-label={`Move row ${ri + 1} up`}
											disabled={ri === 0}
											onclick={() => editor.shiftRow(section.id, row.id, -1)}
										>
											<Icon name="chevron-up" size={16} />
										</button>
										<button
											type="button"
											class="btn btn-ghost btn-icon"
											aria-label={`Move row ${ri + 1} down`}
											disabled={ri === rs.rows.length - 1}
											onclick={() => editor.shiftRow(section.id, row.id, 1)}
										>
											<Icon name="chevron-down" size={16} />
										</button>
										<button
											type="button"
											class="btn btn-ghost btn-icon danger"
											aria-label={`Delete row ${ri + 1}`}
											onclick={() => removeRow(section, row)}
										>
											<Icon name="trash" size={16} />
										</button>
									</div>
									<div class="pgrid" style:--cols={Math.min(row.columns.length, cap)}>
										{#each row.columns as column, ci (column.id)}
											<div
												class="pcol"
												class:zone={dnd.zone === column.id &&
													dnd.target?.type === 'items' &&
													dnd.target.panel?.column === column.id}
												data-items={section.id}
												data-row={row.id}
												data-column={column.id}
												role="group"
												aria-label={`Row ${ri + 1}, column ${ci + 1}`}
											>
												{#each column.controls as rc (itemId(rc.item))}
													{@render card(rc, section)}
												{/each}
												{#if column.controls.length === 0}
													<p class="empty-grid muted">
														Empty column. Drag controls here, or use the column arrows.
													</p>
												{/if}
												{#if row.columns.length > 1}
													<button
														type="button"
														class="btn btn-ghost colx"
														onclick={() => removeColumn(section, row.id, column)}
													>
														<Icon name="trash" size={14} /> Column
													</button>
												{/if}
											</div>
										{/each}
									</div>
								</div>
							{/each}
							<button
								type="button"
								class="btn addrow"
								disabled={rs.rows.length >= MAX_ROWS}
								onclick={() => editor.addRow(section.id)}
							>
								<Icon name="plus" size={16} /> Row
							</button>
						</div>
					{:else}
						<div
							class="grid"
							class:zone={dnd.zone === section.id &&
								dnd.target?.type === 'items' &&
								!dnd.target.panel}
							style:--cols={Math.min(section.mode === 'panels' ? 1 : section.columns, cap)}
							data-items={section.id}
						>
							{#each rs.controls as rc (itemId(rc.item))}
								{@render card(rc, section)}
							{/each}
							{#if rs.controls.length === 0}
								<p class="empty-grid muted">Empty. Drag controls here, or use Library, then Add.</p>
							{/if}
						</div>
					{/if}
				</section>
			{/each}

			{#if resolved.unplaced.length > 0}
				<details class="card stack">
					<summary>Unplaced ({resolved.unplaced.length})</summary>
					<p class="muted small">
						These controls appear under More on the run page. Add places them in the selected
						section.
					</p>
					{#each resolved.unplaced as control (control.binding_id)}
						<div class="row wrap">
							<span
								class="grip"
								title="Drag to move"
								aria-hidden="true"
								{@attach dnd.handle(() => ({
									type: 'lib',
									id: control.binding_id,
									label: control.label
								}))}
							>
								<Icon name="grip" size={18} />
							</span>
							<span>{control.label} <code>{control.binding_id}</code></span>
							<button
								type="button"
								class="btn"
								disabled={!editor.canPlace(control.binding_id)}
								onclick={() => editor.addControl(control.binding_id)}
								aria-label={`Add ${control.label} from Unplaced`}>Add</button
							>
							<button
								type="button"
								class="btn"
								disabled={!editor.canPlace(control.binding_id)}
								onclick={() => editor.hideControl(control.binding_id)}
								aria-label={`Hide ${control.label} from Unplaced`}>Hide</button
							>
						</div>
					{/each}
				</details>
			{/if}

			<div class="row add">
				<button
					type="button"
					class="btn"
					disabled={editor.sectionsFull}
					title={editor.sectionsFull ? editor.sectionLimitHint : undefined}
					onclick={() => editor.createSection()}
				>
					<Icon name="plus" size={16} /> Add section
				</button>
				{#if resolved.hidden.length > 0}
					<span class="muted small">{resolved.hidden.length} hidden</span>
				{/if}
			</div>
		{/if}
	</div>
</div>

<style>
	.canvas {
		flex: 1 1 auto;
		min-height: 0;
		overflow-y: auto;
		scrollbar-gutter: stable;
		overscroll-behavior: contain;
		padding: var(--space-3);
		background: var(--color-bg);
	}
	/* Phone: room to lift the last control above the half-height inspector. */
	.canvas.compact {
		padding-bottom: 50dvh;
	}
	.frame {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		width: 100%;
		margin: 0 auto;
	}
	.frame.constrained {
		padding: var(--space-3);
		background: var(--color-surface-1);
		border: 1px solid var(--color-border-strong);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-2);
	}
	.small {
		font-size: var(--text-xs);
	}

	.banner {
		max-width: 52rem;
		margin: 0 auto var(--space-3);
		border-color: var(--color-warning);
	}
	.banner-title {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		margin: 0;
		font-weight: 650;
		color: var(--color-warning);
	}
	.banner ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	.banner li {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
	}
	.banner code {
		overflow-wrap: anywhere;
	}

	.empty h2 {
		margin: 0;
	}

	.sec {
		background: var(--color-surface-1);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-1);
		min-width: 0;
	}
	.sec.selected {
		border-color: var(--color-accent);
	}
	.sec.target {
		box-shadow: 0 0 0 2px var(--color-accent-soft);
	}
	.sec.dragging {
		opacity: 0.45;
	}

	header {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-1) var(--space-2);
		padding: var(--space-1) var(--space-2);
		border-bottom: 1px solid var(--color-border);
	}
	.title {
		flex: 1 1 8rem;
		min-width: 0;
		font-weight: 650;
		background: transparent;
		border-color: transparent;
	}
	.title:hover:not(:focus) {
		background: var(--color-surface-2);
	}
	.spacer {
		flex: 0 0 0;
		margin-left: auto;
	}
	.danger {
		color: var(--color-danger);
	}
	.collapse {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		min-height: var(--control-h);
		font-size: var(--text-sm);
		color: var(--color-text-muted);
		cursor: pointer;
	}

	.seg {
		display: inline-flex;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		overflow: hidden;
	}
	.seg button {
		min-width: var(--control-h);
		min-height: var(--control-h);
		padding: 0 0.5rem;
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

	.grip {
		flex: none;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: var(--touch-target);
		height: var(--touch-target);
		margin: calc(var(--space-1) * -1) 0;
		color: var(--color-text-faint);
		cursor: grab;
		touch-action: none;
		user-select: none;
	}
	@media (pointer: fine) {
		.grip {
			width: 1.75rem;
			height: 1.75rem;
			margin: 0;
		}
	}
	.grip:hover {
		color: var(--color-text);
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(var(--cols, 1), minmax(0, 1fr));
		gap: var(--space-3);
		padding: var(--space-3);
		min-height: 4rem;
		align-items: start;
	}
	.grid.zone,
	.pcol.zone {
		background: var(--color-accent-soft);
		border-radius: var(--radius);
	}
	.empty-grid {
		grid-column: 1 / -1;
		margin: 0;
		padding: var(--space-3);
		text-align: center;
		font-size: var(--text-sm);
		border: 1px dashed var(--color-border-strong);
		border-radius: var(--radius);
	}

	.item {
		min-width: 0;
		padding: 0 var(--space-2) var(--space-2);
		background: var(--color-surface-2);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		touch-action: pan-y;
		cursor: default;
	}
	.item.full {
		grid-column: 1 / -1;
	}
	.item.selected {
		border-color: var(--color-accent);
		box-shadow: 0 0 0 2px var(--color-accent-soft);
	}
	.item.dragging {
		opacity: 0.4;
	}
	.bar {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		min-height: 1.75rem;
		margin-left: calc(var(--space-2) * -1);
	}
	.pick {
		flex: 1 1 auto;
		min-width: 0;
		display: block;
		min-height: 0;
		padding: 0;
		text-align: left;
		background: none;
		border: 0;
		cursor: pointer;
		color: var(--color-text-faint);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.pick code {
		font-size: var(--text-xs);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-1);
		margin-top: var(--space-2);
		padding-top: var(--space-2);
		border-top: 1px dashed var(--color-border-strong);
	}
	.moveto {
		width: auto;
		min-width: 7rem;
		flex: 1 1 7rem;
		max-width: 12rem;
	}

	.panels {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-2);
	}
	.prow {
		border: 1px dashed var(--color-border-strong);
		border-radius: var(--radius-lg);
		padding: var(--space-1) var(--space-2) var(--space-2);
		min-width: 0;
	}
	.rowbar {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin-bottom: var(--space-1);
	}
	.pgrid {
		display: grid;
		grid-template-columns: repeat(var(--cols, 1), minmax(0, 1fr));
		gap: var(--space-2);
	}
	.pcol {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-width: 0;
		padding: var(--space-2);
		background: var(--color-bg);
		border-radius: var(--radius-sm);
	}
	.colx {
		align-self: flex-start;
		font-size: var(--text-xs);
	}
	.addrow {
		align-self: flex-start;
	}

	/* Sits in the flex gap between cards; faint until hovered or focused so touch still finds it. */
	.insert {
		display: flex;
		justify-content: center;
		align-items: center;
		height: 0;
		margin: calc(var(--space-3) / -2) 0;
		position: relative;
		z-index: 1;
	}
	.insert button {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		min-height: 1.75rem;
		padding: 0 var(--space-2);
		font-size: var(--text-xs);
		color: var(--color-text-muted);
		background: var(--color-surface-1);
		border: 1px dashed var(--color-border-strong);
		border-radius: var(--radius-full);
		cursor: pointer;
		opacity: 0.35;
		transition: opacity 0.12s;
	}
	.insert button span {
		display: none;
	}
	.insert:hover button,
	.insert button:focus-visible {
		opacity: 1;
		border-color: var(--color-accent);
	}
	.insert:hover button span,
	.insert button:focus-visible span {
		display: inline;
	}
	@media (hover: none) {
		.insert button {
			opacity: 0.7;
		}
	}

	.add {
		justify-content: flex-start;
		padding-bottom: var(--space-4);
	}
</style>
