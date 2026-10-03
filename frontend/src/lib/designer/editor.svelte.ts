// Central state of the layout designer (docs/UI_DESIGNER.md "Designer").
//
// One `Designer` per workflow page owns everything the panels share:
//   - the server snapshot: control schema (saved corrections already replayed),
//     saved layout + its revision, per-correction revisions;
//   - the working layout doc inside a History (undo/redo of immutable snapshots);
//   - sparse presentation drafts per binding (see presentation.ts), live in the
//     canvas preview but only persisted by save();
//   - selection, the target section for "Add", preview width, and save status.
//
// Dirty = the working doc is not the saved doc (reference compare, so undoing
// back to the saved snapshot is clean) or any draft differs from the schema.
import { tick } from 'svelte';
import { api, ApiRequestError, describeApiError } from '$lib/api';
import type {
	Correction,
	ControlDescriptor,
	ControlSchema,
	EditValue,
	LayoutDoc,
	Page,
	WorkflowInfo,
	WorkflowLayout
} from '$lib/contracts';
import {
	addSection,
	addRow,
	removeColumn,
	removeRow,
	sectionItems,
	setRowColumns,
	setSectionMode,
	shiftRow,
	type PanelTarget,
	canAddSection,
	canPlace,
	defaultLayout,
	hide,
	locate,
	itemId,
	itemBindings,
	pairDimensions,
	splitDimensions,
	moveItem,
	moveSection,
	newSectionId,
	placeControl,
	removeSection,
	removeStale,
	resolveLayout,
	setSpan,
	setToggle,
	setWhen,
	unhide,
	unplace,
	updateSection,
	validate,
	MAX_ITEMS,
	MAX_SECTIONS
} from '$lib/layout/model';
import { History } from '$lib/layout/history.svelte';
import { copyKnown, describeCopy } from './copyLayout';
import { layoutChanges } from './conflict';
import { dropIndex } from './drop';
import {
	applyDraft,
	buildPresentation,
	normalizePatch,
	type DraftPatch
} from './presentation';
import { baseValue, sameValue } from '$lib/run/values';

export type PreviewWidth = 'phone' | 'tablet' | 'full';
export const PREVIEW_PX: Record<PreviewWidth, number | null> = { phone: 390, tablet: 820, full: null };

export type Selection = { kind: 'control' | 'section'; id: string } | null;

export interface Failure {
	key: string;
	label: string;
	message: string;
	conflict: boolean;
}

export type Placement = { kind: 'section'; id: string; title: string } | { kind: 'hidden' };

const EMPTY_DOC: LayoutDoc = { version: 1, sections: [], hidden: [] };
const JSON_HEADERS = { 'content-type': 'application/json' };

export class Designer {
	readonly workflowId: string;

	name = $state('');
	schema = $state.raw<ControlSchema | null>(null);
	loading = $state(true);
	loadError = $state<string | null>(null);

	readonly history = new History<LayoutDoc>();
	/** The doc as last saved (or loaded). Reference-compared with the working doc. */
	savedDoc = $state.raw<LayoutDoc>(EMPTY_DOC);
	layoutRevision = $state(0);
	/** A layout is stored server-side (otherwise the run page uses the automatic one). */
	persisted = $state(false);
	/** Workflow-scope corrections by binding id, each with its own revision. */
	savedCorrections = $state.raw<Record<string, Correction>>({});
	/** Sparse presentation edits by binding id; only real differences are kept. */
	drafts = $state.raw<Record<string, DraftPatch>>({});
	/** Throwaway values typed into canvas previews. Never saved or submitted. */
	previewValues = $state.raw<Record<string, EditValue>>({});

	selection = $state.raw<Selection>(null);
	targetSectionId = $state<string | null>(null);
	previewWidth = $state<PreviewWidth>('full');

	saving = $state(false);
	busy = $state(false);
	failures = $state.raw<Failure[]>([]);
	/** The layout PUT hit 409: offer reload or overwrite. */
	layoutConflict = $state(false);
	conflictSnapshot = $state.raw<{ layout: WorkflowLayout; corrections: Correction[] } | null>(null);
	conflictChanges = $state.raw<string[]>([]);
	/** Outcome of the last "Copy layout from…", shown under the toolbar until dismissed. */
	info = $state<string | null>(null);
	/** Polite live-region text for moves and edits. */
	announcement = $state('');
	private announceTick = false;
	/** The last 409 came from "Reset to automatic": "keep mine" retries the reset. */
	private resetConflict = false;

	readonly doc = $derived(this.history.current ?? EMPTY_DOC);
	readonly resolved = $derived(this.schema ? resolveLayout(this.schema, this.doc) : null);
	readonly controlsById = $derived(
		new Map((this.schema?.controls ?? []).map((c) => [c.binding_id, c] as const))
	);
	/** Backend caps: 40 sections, 1000 placed + hidden controls. Buttons disable instead of failing at Save. */
	readonly sectionsFull = $derived(!canAddSection(this.doc));
	readonly sectionLimitHint = `At most ${MAX_SECTIONS} sections`;
	readonly itemLimitHint = `A layout holds at most ${MAX_ITEMS} controls`;
	readonly layoutDirty = $derived(this.doc !== this.savedDoc);
	readonly dirtyBindings = $derived(Object.keys(this.drafts));
	/** Controls whose workflow default (graph value) was changed in the designer. */
	readonly dirtyValues = $derived(Object.keys(this.previewValues));
	readonly dirty = $derived(this.layoutDirty || this.dirtyBindings.length > 0 || this.dirtyValues.length > 0);
	readonly conflicts = $derived(this.layoutConflict || this.failures.some((f) => f.conflict));
	/** Nothing saved and nothing placed: offer the two starting points. */
	readonly fresh = $derived(
		!this.persisted && this.doc.sections.length === 0 && this.doc.hidden.length === 0
	);
	/** Where each placed or hidden binding lives. */
	readonly placement = $derived.by(() => {
		const map = new Map<string, Placement>();
		for (const section of this.doc.sections) {
			if (section.toggle) map.set(section.toggle, { kind: 'section', id: section.id, title: section.title });
			for (const item of sectionItems(section)) {
				for (const id of itemBindings(item)) map.set(id, { kind: 'section', id: section.id, title: section.title });
			}
		}
		for (const id of this.doc.hidden) map.set(id, { kind: 'hidden' });
		return map;
	});

	constructor(workflowId: string) {
		this.workflowId = workflowId;
	}

	// --- loading ----------------------------------------------------------

	async load(): Promise<void> {
		this.loading = true;
		this.loadError = null;
		try {
			const id = this.workflowId;
			const [schema, layout, corrections, list] = await Promise.all([
				api<ControlSchema>(`/workflows/${id}/controls`),
				api<WorkflowLayout>(`/workflows/${id}/layout`),
				api<Correction[]>(`/workflows/${id}/corrections`),
				api<Page<WorkflowInfo>>('/workflows?limit=200').catch(() => null)
			]);
			this.schema = schema;
			this.name = list?.items.find((w) => w.id === id)?.name ?? '';
			this.setCorrections(corrections);
			const doc = layout.layout ?? EMPTY_DOC;
			this.history.reset(doc);
			this.savedDoc = doc;
			this.layoutRevision = layout.revision;
			this.persisted = layout.layout !== null;
			this.drafts = {};
			this.previewValues = {};
			this.selection = null;
			this.targetSectionId = null;
			this.failures = [];
			this.layoutConflict = false;
			this.conflictSnapshot = null;
		} catch (cause) {
			this.loadError = describeApiError(cause);
		} finally {
			this.loading = false;
		}
	}

	private setCorrections(corrections: Correction[]): void {
		this.savedCorrections = Object.fromEntries(
			corrections.filter((c) => c.scope === 'workflow').map((c) => [c.selector, c])
		);
	}

	/** Refetch schema + corrections (after saving or resetting a presentation) and re-prune drafts. */
	private async refreshSchema(): Promise<void> {
		const id = this.workflowId;
		const [schema, corrections] = await Promise.all([
			api<ControlSchema>(`/workflows/${id}/controls`),
			api<Correction[]>(`/workflows/${id}/corrections`)
		]);
		this.schema = schema;
		this.setCorrections(corrections);
		const next: Record<string, DraftPatch> = {};
		for (const [binding, patch] of Object.entries(this.drafts)) {
			const control = schema.controls.find((c) => c.binding_id === binding);
			const kept = control ? normalizePatch(control, patch) : {};
			if (Object.keys(kept).length) next[binding] = kept;
		}
		this.drafts = next;
	}

	// --- announcements ----------------------------------------------------

	announce(message: string): void {
		// A trailing zero-width toggle makes an identical message re-announce.
		this.announceTick = !this.announceTick;
		this.announcement = message + (this.announceTick ? '​' : '');
	}

	// --- history ----------------------------------------------------------

	/** Record a new working doc (no-op when unchanged). */
	commit(next: LayoutDoc, message?: string): void {
		if (next === this.doc) return;
		this.history.push(next);
		if (message) this.announce(message);
	}

	undo(): void {
		if (this.history.undo()) this.afterHistory('Undone');
	}

	redo(): void {
		if (this.history.redo()) this.afterHistory('Redone');
	}

	private afterHistory(message: string): void {
		const sel = this.selection;
		if (sel?.kind === 'section' && !this.doc.sections.some((s) => s.id === sel.id)) this.selection = null;
		if (this.targetSectionId && !this.doc.sections.some((s) => s.id === this.targetSectionId)) {
			this.targetSectionId = null;
		}
		this.announce(message);
	}

	// --- selection and targets ---------------------------------------------

	selectControl(id: string): void {
		this.selection = { kind: 'control', id };
		const where = locate(this.doc, id);
		if (where && where !== 'hidden') this.targetSectionId = where.section;
	}

	selectSection(id: string): void {
		this.selection = { kind: 'section', id };
		this.targetSectionId = id;
	}

	clearSelection(): void {
		this.selection = null;
	}

	/** Scroll a freshly added or moved control into view once it is rendered. */
	async reveal(id: string): Promise<void> {
		await tick();
		const where = locate(this.doc, id);
		const section = where && where !== 'hidden' ? this.doc.sections.find((s) => s.id === where.section) : undefined;
		const item = section ? sectionItems(section).find((it) => itemBindings(it).includes(id)) : null;
		const element = document.querySelector(`[data-item="${CSS.escape(id)}"]`) ?? (item ? document.querySelector(`[data-item="${CSS.escape(itemId(item))}"]`) : null);
		element?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
	}

	private sectionTitle(id: string): string {
		return this.doc.sections.find((s) => s.id === id)?.title ?? 'section';
	}

	// --- layout edits ------------------------------------------------------

	startFromAutomatic(): void {
		if (!this.schema) return;
		this.commit(defaultLayout(this.schema), 'Started from the automatic layout');
	}

	startEmpty(): void {
		this.createSection('New section');
	}

	/** Insert a section at `index` (default: append), select it and make it the add target. Returns its id, or null at the cap. */
	createSection(title = 'New section', index?: number): string | null {
		if (this.sectionsFull) return null;
		const id = newSectionId(this.doc.sections.map((s) => s.id));
		this.commit(addSection(this.doc, title, index, id), `Added section ${title}`);
		this.selectSection(id);
		return id;
	}

	updateSection(id: string, patch: Parameters<typeof updateSection>[2]): void {
		this.commit(updateSection(this.doc, id, patch));
	}

	deleteSection(id: string): void {
		const title = this.sectionTitle(id);
		this.commit(removeSection(this.doc, id), `Deleted section ${title}`);
		if (this.selection?.kind === 'section' && this.selection.id === id) this.selection = null;
		if (this.targetSectionId === id) this.targetSectionId = null;
	}

	/** Shift a section by `delta` positions (-1 up, +1 down). */
	shiftSection(id: string, delta: number): void {
		const from = this.doc.sections.findIndex((s) => s.id === id);
		const to = from + delta;
		if (from < 0 || to < 0 || to >= this.doc.sections.length) return;
		this.commit(moveSection(this.doc, from, to), `Moved section ${this.sectionTitle(id)} to position ${to + 1}`);
	}

	/** Put a control in the target section (the last selected, else the first, else a new one). */
	addControl(id: string): void {
		let doc = this.doc;
		let target = this.targetSectionId;
		if (!target || !doc.sections.some((s) => s.id === target)) target = doc.sections[0]?.id ?? null;
		if (!target) {
			target = newSectionId(doc.sections.map((s) => s.id));
			doc = addSection(doc, 'New section', undefined, target);
		}
		const section = doc.sections.find((s) => s.id === target);
		if (section?.mode === 'panels' && section.rows.length === 0) doc = addRow(doc, target);
		doc = placeControl(doc, id, target);
		const label = this.controlsById.get(id)?.label ?? id;
		const title = doc.sections.find((s) => s.id === target)?.title ?? 'section';
		this.commit(doc, `Added ${label} to ${title}`);
		this.targetSectionId = target;
		this.selection = { kind: 'control', id };
		void this.reveal(id);
	}

	/** Final index among a section's items for a drop before the `domIndex`-th rendered control. */
	private dropIndex(sectionId: string, binding: string, domIndex: number, target?: PanelTarget): number | undefined {
		const rendered = this.resolved?.sections.find((s) => s.section.id === sectionId);
		const section = this.doc.sections.find((s) => s.id === sectionId);
		if (!rendered || !section) return undefined;
		const row = section.mode === 'panels' ? section.rows.find((r) => target ? r.id === target.row : true) : undefined;
		const column = row?.columns.find((c) => target ? c.id === target.column : true);
		const items = section.mode === 'panels' ? column?.items : section.items;
		const controls = section.mode === 'panels'
			? rendered.rows?.find((r) => r.id === row?.id)?.columns.find((c) => c.id === column?.id)?.controls
			: rendered.controls;
		if (!items || !controls) return undefined;
		return dropIndex(items, controls.map((rc) => itemId(rc.item)), binding, domIndex);
	}

	/** False when adding/hiding this control would exceed the item cap. */
	canPlace(binding: string): boolean {
		return canPlace(this.doc, binding);
	}

	/** Place or move a control into a section at a rendered position (drag-and-drop, "Move to…"). */
	placeAt(binding: string, sectionId: string, domIndex?: number, target?: PanelTarget): void {
		const index = domIndex === undefined ? undefined : this.dropIndex(sectionId, binding, domIndex, target);
		let doc = this.doc;
		const destination = doc.sections.find((s) => s.id === sectionId);
		if (destination?.mode === 'panels' && destination.rows.length === 0) doc = addRow(doc, sectionId);
		const next = placeControl(doc, binding, sectionId, index, target);
		const label = this.controlsById.get(binding)?.label ?? binding;
		const section = next.sections.find((s) => s.id === sectionId);
		const where = locate(next, binding);
		const position = (where && where !== 'hidden' ? where.index : 0) + 1;
		this.commit(next, `Moved ${label} to ${section?.title ?? 'section'}, position ${position}`);
		this.selectControl(binding);
		void this.reveal(binding);
	}

	/** Move the section at `from` so it ends at final index `to` (drag-and-drop). */
	reorderSection(id: string, to: number): void {
		const from = this.doc.sections.findIndex((s) => s.id === id);
		if (from < 0) return;
		this.commit(
			moveSection(this.doc, from, to),
			`Moved section ${this.sectionTitle(id)} to position ${Math.min(to, this.doc.sections.length - 1) + 1}`
		);
	}

	/** Move a control one visible place up (-1) or down (+1) within its section (panels: its column). */
	shiftItem(binding: string, delta: number): void {
		const where = locate(this.doc, binding);
		if (where === null || where === 'hidden' || where.index < 0) return;
		const section = this.doc.sections.find((s) => s.id === where.section);
		const rendered = this.resolved?.sections.find((s) => s.section.id === where.section);
		if (!section || !rendered) return;
		const target: PanelTarget | undefined = where.row && where.column ? { row: where.row, column: where.column } : undefined;
		const items = section.mode === 'panels'
			? section.rows.find((r) => r.id === where.row)?.columns.find((c) => c.id === where.column)?.items ?? []
			: section.items;
		// Stale items are not rendered, so step over them.
		const renderedIds = new Set(rendered.controls.map((rc) => itemId(rc.item)));
		const visible = items.map(itemId).filter((id) => renderedIds.has(id));
		const neighbour = visible[visible.indexOf(itemId(items[where.index])) + delta];
		if (neighbour === undefined) return;
		const to = items.findIndex((i) => itemId(i) === neighbour);
		const next = moveItem(this.doc, binding, where.section, to, target);
		const label = this.controlsById.get(binding)?.label ?? binding;
		this.commit(next, `Moved ${label} ${delta < 0 ? 'up' : 'down'}`);
		void this.reveal(binding);
	}

	/** Panels: move a control to the end of the previous (-1) or next (+1) column, in row-major order. */
	shiftColumn(binding: string, delta: number): void {
		const where = locate(this.doc, binding);
		if (where === null || where === 'hidden' || !where.row) return;
		const section = this.doc.sections.find((s) => s.id === where.section);
		if (section?.mode !== 'panels') return;
		const cells = section.rows.flatMap((r) => r.columns.map((c) => ({ row: r.id, column: c.id })));
		const target = cells[cells.findIndex((c) => c.row === where.row && c.column === where.column) + delta];
		if (!target) return;
		const label = this.controlsById.get(binding)?.label ?? binding;
		this.commit(moveItem(this.doc, binding, section.id, undefined, target), `Moved ${label} to the ${delta < 0 ? 'previous' : 'next'} column`);
		void this.reveal(binding);
	}

	/** Whether shiftColumn(binding, delta) has somewhere to go. */
	canShiftColumn(binding: string, delta: number): boolean {
		const where = locate(this.doc, binding);
		if (where === null || where === 'hidden' || !where.row) return false;
		const section = this.doc.sections.find((s) => s.id === where.section);
		if (section?.mode !== 'panels') return false;
		const cells = section.rows.flatMap((r) => r.columns.map((c) => `${r.id}/${c.id}`));
		const at = cells.indexOf(`${where.row}/${where.column}`) + delta;
		return at >= 0 && at < cells.length;
	}

	// --- panel structure ----------------------------------------------------

	setMode(sectionId: string, mode: 'auto' | 'panels'): void {
		this.commit(setSectionMode(this.doc, sectionId, mode), `${this.sectionTitle(sectionId)} now uses ${mode === 'panels' ? 'rows and columns' : 'automatic flow'}`);
	}

	addRow(sectionId: string, columns = 1): void {
		this.commit(addRow(this.doc, sectionId, columns), `Added a row to ${this.sectionTitle(sectionId)}`);
	}

	removeRow(sectionId: string, rowId: string): void {
		this.commit(removeRow(this.doc, sectionId, rowId), 'Deleted row; its controls are unplaced');
	}

	shiftRow(sectionId: string, rowId: string, delta: number): void {
		this.commit(shiftRow(this.doc, sectionId, rowId, delta), `Moved row ${delta < 0 ? 'up' : 'down'}`);
	}

	setRowColumns(sectionId: string, rowId: string, count: number): void {
		this.commit(setRowColumns(this.doc, sectionId, rowId, count), `Row now has ${count} column${count > 1 ? 's' : ''}`);
	}

	removeColumn(sectionId: string, rowId: string, columnId: string): void {
		this.commit(removeColumn(this.doc, sectionId, rowId, columnId), 'Deleted column; its controls are unplaced');
	}

	pair(width: string, height: string): void {
		if ([width, height].some((id) => this.controlsById.get(id)?.logical_type !== 'int')) return;
		this.commit(pairDimensions(this.doc, width, height), 'Created aspect-ratio control');
	}

	split(binding: string): void { this.commit(splitDimensions(this.doc, binding), 'Split dimension controls'); }

	/** Use a boolean control as the section's header switch (null clears it). */
	setToggle(sectionId: string, binding: string | null): void {
		const label = binding ? (this.controlsById.get(binding)?.label ?? binding) : null;
		const title = this.sectionTitle(sectionId);
		this.commit(setToggle(this.doc, sectionId, binding), label ? `${label} now switches ${title}` : `Removed the switch from ${title}`);
	}

	/** Show a placed control only while a boolean control is on (null: always). */
	setWhen(binding: string, when: string | null): void {
		const label = this.controlsById.get(binding)?.label ?? binding;
		this.commit(setWhen(this.doc, binding, when), when ? `${label} now shows only when ${this.controlsById.get(when)?.label ?? when} is on` : `${label} always shows`);
	}

	setSpan(binding: string, span: 'auto' | 'full'): void {
		this.commit(setSpan(this.doc, binding, span));
	}

	hideControl(binding: string): void {
		const label = this.controlsById.get(binding)?.label ?? binding;
		this.commit(hide(this.doc, binding), `Hid ${label}`);
	}

	hideNode(nodeId: string): void {
		let next = this.doc;
		for (const control of this.schema?.controls ?? []) {
			if (control.node_id !== nodeId) continue;
			if (!canPlace(next, control.binding_id)) {
				this.announce(this.itemLimitHint);
				return;
			}
			next = hide(next, control.binding_id);
		}
		this.commit(next, `Hid all controls of node ${nodeId}`);
	}

	/** One undoable step: a new section titled after the node, holding its non-hidden controls. */
	addNodeSection(nodeId: string, title: string): void {
		if (!canAddSection(this.doc)) {
			this.announce(`A layout holds at most ${MAX_SECTIONS} sections`);
			return;
		}
		const id = newSectionId(this.doc.sections.map((s) => s.id));
		let next = addSection(this.doc, title, undefined, id);
		const controls = (this.schema?.controls ?? [])
			.filter((c) => c.node_id === nodeId && !next.hidden.includes(c.binding_id))
			.sort((a, b) => a.order - b.order);
		for (const control of controls) {
			if (!canPlace(next, control.binding_id)) {
				this.announce(this.itemLimitHint);
				return;
			}
			next = placeControl(next, control.binding_id, id);
		}
		this.commit(next, `Added section ${title} with ${controls.length} controls`);
		this.selectSection(id);
	}

	unhideControl(binding: string): void {
		const label = this.controlsById.get(binding)?.label ?? binding;
		this.commit(unhide(this.doc, binding), `Unhid ${label}; it is unplaced`);
	}

	unplaceControl(binding: string): void {
		this.commit(unplace(this.doc, binding));
	}

	removeStaleBinding(binding: string): void {
		this.commit(removeStale(this.doc, binding), `Removed stale control ${binding}`);
	}

	// --- presentation drafts -----------------------------------------------

	setDraft(binding: string, patch: DraftPatch): void {
		const control = this.controlsById.get(binding);
		if (!control) return;
		const merged = normalizePatch(control, { ...this.drafts[binding], ...patch });
		const { [binding]: _drop, ...rest } = this.drafts;
		void _drop;
		this.drafts = Object.keys(merged).length ? { ...rest, [binding]: merged } : rest;
		if ('display_default' in patch) {
			const { [binding]: _value, ...values } = this.previewValues;
			void _value;
			this.previewValues = values;
		}
	}

	/** The descriptor with this binding's draft applied (what the canvas previews). */
	preview(control: ControlDescriptor): ControlDescriptor {
		return applyDraft(control, this.drafts[control.binding_id]);
	}

	previewValue(control: ControlDescriptor): EditValue {
		return this.previewValues[control.binding_id] ?? control.value ?? '';
	}

	/**
	 * Change a control's workflow default. Saved as a new graph revision, so it
	 * works for every type; a legacy numeric display default would override the
	 * shown value, so it is cleared alongside.
	 */
	setPreviewValue(binding: string, value: EditValue): void {
		const control = this.controlsById.get(binding);
		if (!control) return;
		if (this.savedCorrections[binding]?.presentation.display_default != null) this.setDraft(binding, { display_default: '' });
		const { [binding]: _drop, ...rest } = this.previewValues;
		void _drop;
		this.previewValues = sameValue(control, value, baseValue(control)) ? rest : { ...rest, [binding]: value };
	}

	/** Drop an unsaved default change. */
	revertValue(binding: string): void {
		const { [binding]: _drop, ...rest } = this.previewValues;
		void _drop;
		this.previewValues = rest;
	}

	/** True when a saved correction or an unsaved draft exists for the binding. */
	hasPresentation(binding: string): boolean {
		return binding in this.savedCorrections || binding in this.drafts;
	}

	/** Drop the draft and DELETE the saved correction (reflected immediately). */
	async resetPresentation(binding: string): Promise<void> {
		this.busy = true;
		try {
			const { [binding]: _drop, ...rest } = this.drafts;
			void _drop;
			this.drafts = rest;
			if (binding in this.savedCorrections) {
				await api(
					`/workflows/${this.workflowId}/corrections?scope=workflow&selector=${encodeURIComponent(binding)}`,
					{ method: 'DELETE' }
				);
			}
			await this.refreshSchema();
			this.announce('Presentation reset');
		} catch (cause) {
			this.failures = [
				...this.failures,
				{ key: binding, label: binding, message: describeApiError(cause), conflict: false }
			];
		} finally {
			this.busy = false;
		}
	}

	// --- copy from another workflow ------------------------------------------

	/**
	 * Replace the working layout with another workflow's saved one, restricted to
	 * bindings that exist here. It is an ordinary unsaved edit (Undo restores).
	 * Returns an error/notice message when nothing was copied, else null.
	 */
	async copyLayoutFrom(source: WorkflowInfo): Promise<string | null> {
		if (!this.schema) return 'The workflow is not loaded.';
		try {
			const other = await api<WorkflowLayout>(`/workflows/${source.id}/layout`);
			if (!other.layout) return `"${source.name}" has no saved layout, so there is nothing to copy.`;
			const result = copyKnown(other.layout, this.schema);
			this.commit(result.doc);
			this.selection = null;
			this.targetSectionId = null;
			this.info = describeCopy(source.name, result);
			this.announce(this.info);
			return null;
		} catch (cause) {
			return describeApiError(cause);
		}
	}

	// --- reset to automatic -------------------------------------------------

	/** DELETE the saved layout and show the automatic one. The undo stack is kept. */
	async resetToAutomatic(): Promise<void> {
		if (!this.schema) return;
		this.busy = true;
		this.resetConflict = false;
		try {
			if (this.persisted) {
				await api(`/workflows/${this.workflowId}/layout?expected_revision=${this.layoutRevision}`, {
					method: 'DELETE'
				});
			}
			const auto = defaultLayout(this.schema);
			this.history.push(auto);
			this.savedDoc = auto;
			this.layoutRevision = 0;
			this.persisted = false;
			this.selection = null;
			this.targetSectionId = null;
			this.layoutConflict = false;
			this.announce('Reset to the automatic layout');
		} catch (cause) {
			const conflict = cause instanceof ApiRequestError && cause.status === 409;
			this.layoutConflict = conflict;
			this.resetConflict = conflict;
			this.failures = [
				...this.failures,
				{
					key: 'layout',
					label: 'Layout',
					message: conflict
						? 'The layout was changed elsewhere since you opened it.'
						: describeApiError(cause),
					conflict
				}
			];
		} finally {
			if (this.layoutConflict) await this.inspectConflict();
			this.busy = false;
		}
	}

	// --- saving -------------------------------------------------------------

	/** PUT the layout (if dirty), then each dirty correction one at a time. */
	async save(): Promise<void> {
		if (this.saving || !this.dirty) return;
		this.saving = true;
		this.failures = [];
		this.layoutConflict = false;
		this.resetConflict = false;
		this.conflictSnapshot = null;
		const failures: Failure[] = [];
		// The exact draft objects that were sent: an edit made while the PUT was in
		// flight replaces the object, so it is kept (and the form stays dirty).
		const saved: [string, DraftPatch][] = [];
		let valuesSaved = false;
		try {
			if (this.layoutDirty) {
				const sent = this.doc;
				const problems = validate(sent);
				if (problems.length) {
					failures.push({ key: 'layout', label: 'Layout', message: problems.join('; '), conflict: false });
				} else {
					try {
						const result = await api<WorkflowLayout>(`/workflows/${this.workflowId}/layout`, {
							method: 'PUT',
							headers: JSON_HEADERS,
							body: JSON.stringify({ layout: sent, expected_revision: this.layoutRevision })
						});
						this.layoutRevision = result.revision;
						this.savedDoc = sent;
						this.persisted = true;
					} catch (cause) {
						const conflict = cause instanceof ApiRequestError && cause.status === 409;
						this.layoutConflict = conflict;
						failures.push({
							key: 'layout',
							label: 'Layout',
							message: conflict
								? 'The layout was changed elsewhere since you opened it.'
								: describeApiError(cause),
							conflict
						});
					}
				}
			}
			if (!this.layoutConflict) {
				for (const binding of Object.keys(this.drafts)) {
					const patch = this.drafts[binding];
					const control = this.controlsById.get(binding);
					if (!patch || !control) continue;
					const existing = this.savedCorrections[binding] ?? null;
					try {
						const result = await api<Correction>(`/workflows/${this.workflowId}/corrections`, {
							method: 'PUT',
							headers: JSON_HEADERS,
							body: JSON.stringify({
								scope: 'workflow',
								binding_id: binding,
								presentation: buildPresentation(existing?.presentation ?? null, patch),
								expected_revision: existing?.revision ?? 0
							})
						});
						this.savedCorrections = { ...this.savedCorrections, [binding]: result };
						saved.push([binding, patch]);
					} catch (cause) {
						failures.push({
							key: binding,
							label: control.label,
							message: describeApiError(cause),
							conflict: cause instanceof ApiRequestError && cause.status === 409
						});
					}
				}
			}
			// Defaults last: the corrections above clear any legacy display default first.
			const values = this.previewValues;
			if (!this.layoutConflict && this.schema && Object.keys(values).length) {
				try {
					await api(`/workflows/${this.workflowId}/values`, {
						method: 'PUT',
						headers: JSON_HEADERS,
						body: JSON.stringify({ edits: values, expected_revision: this.schema.revision })
					});
					// Edits made while the PUT was in flight stay dirty.
					if (this.previewValues === values) this.previewValues = {};
					valuesSaved = true;
				} catch (cause) {
					const conflict = cause instanceof ApiRequestError && cause.status === 409;
					failures.push({
						key: 'values',
						label: 'Default values',
						message: conflict ? 'The workflow graph changed elsewhere. Reload to apply your defaults to the new revision.' : describeApiError(cause),
						conflict: false
					});
				}
			}
		} finally {
			this.failures = failures;
			if (saved.length || valuesSaved) {
				const next = { ...this.drafts };
				for (const [binding, sent] of saved) if (next[binding] === sent) delete next[binding];
				this.drafts = next;
				try {
					await this.refreshSchema();
				} catch {
					// The correction is saved; the preview refreshes on the next load.
				}
			}
			if (this.conflicts) await this.inspectConflict();
			this.saving = false;
			if (failures.length === 0) this.announce('Saved');
		}
	}

	/** Fetch what changed before offering an overwrite; retain these exact revisions. */
	async inspectConflict(): Promise<void> {
		this.conflictSnapshot = null;
		this.conflictChanges = [];
		try {
			const id = this.workflowId;
			const [layout, corrections] = await Promise.all([
				api<WorkflowLayout>(`/workflows/${id}/layout`),
				api<Correction[]>(`/workflows/${id}/corrections`)
			]);
			this.conflictChanges = layoutChanges(this.savedDoc, layout.layout ?? EMPTY_DOC);
			for (const correction of corrections.filter((c) => c.scope === 'workflow')) {
				if (correction.revision !== this.savedCorrections[correction.selector]?.revision) {
					this.conflictChanges.push(`Presentation ${correction.selector}: ${JSON.stringify(this.savedCorrections[correction.selector]?.presentation ?? null)} → ${JSON.stringify(correction.presentation)}`);
				}
			}
			for (const id of Object.keys(this.savedCorrections)) {
				if (!corrections.some((c) => c.scope === 'workflow' && c.selector === id)) this.conflictChanges.push(`Presentation removed: ${id}`);
			}
			this.conflictSnapshot = { layout, corrections };
		} catch (cause) {
			this.failures = [...this.failures.filter((failure) => failure.key !== 'overwrite'),
				{ key: 'overwrite', label: 'Refresh', message: describeApiError(cause), conflict: false }
			];
			return;
		}
	}

	async overwrite(): Promise<void> {
		if (!this.conflictSnapshot || this.saving || this.busy) return;
		const { layout, corrections } = this.conflictSnapshot;
		this.layoutRevision = layout.revision;
		this.persisted = layout.layout !== null;
		this.setCorrections(corrections);
		this.conflictSnapshot = null;
		if (this.resetConflict) {
			this.failures = [];
			this.layoutConflict = false;
			await this.resetToAutomatic();
		} else {
			await this.save();
		}
	}

	dismissFailures(): void {
		this.conflictSnapshot = null;
		this.failures = [];
		this.layoutConflict = false;
		this.resetConflict = false;
	}
}
