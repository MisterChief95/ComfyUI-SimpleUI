// Pure layout model for the user-designed generation UI (docs/UI_DESIGNER.md).
// No Svelte, no DOM: node runs it directly (`npm test`), so keep to erasable
// TypeScript only (no enums/namespaces/parameter properties) and use
// `import type` for everything from contracts.
//
// Every operation is immutable: it returns a new LayoutDoc (or the same
// object when it is a no-op) and never touches its input. A binding appears at
// most once across all sections plus `hidden`; the operations preserve that.
import type {
	ControlDescriptor,
	Group,
	LayoutDoc,
	LayoutItem,
	LayoutRow,
	PanelLayoutSection,
	LayoutSection
} from '../contracts.ts';

/** Mirror of backend GROUP_ORDER (mapping/controls.py). */
export const GROUP_ORDER: readonly Group[] = [
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

const COLLAPSED_GROUPS: ReadonlySet<string> = new Set(['advanced', 'output', 'inactive']);

export const MAX_SECTIONS = 40;
export const MAX_ITEMS = 1000;
export const MAX_TITLE = 80;
export const MAX_ROWS = 40;
export const MAX_COLUMNS = 3;
const SECTION_ID = /^[A-Za-z0-9_-]{1,40}$/;

/** Anything with a `controls` list; a ControlSchema fits. */
export interface SchemaLike {
	controls: ControlDescriptor[];
}

export interface ResolvedControl {
	item: LayoutItem;
	control: ControlDescriptor;
	height?: ControlDescriptor;
	/** The boolean control gating this item; absent when ungated or the condition is stale/not boolean. */
	when?: ControlDescriptor;
}

export interface ResolvedSection {
	section: LayoutSection;
	/** Items whose binding exists in the schema, in layout order. */
	controls: ResolvedControl[];
	/** The boolean control in the header; absent when unset or stale/not boolean. */
	toggle?: ControlDescriptor;
	/** Panel sections only: `controls` split by row and column (same entries, same order). */
	rows?: { id: string; columns: { id: string; controls: ResolvedControl[] }[] }[];
}

export interface ResolvedLayout {
	sections: ResolvedSection[];
	/** Schema controls neither placed nor hidden (run page: trailing "More"). */
	unplaced: ControlDescriptor[];
	/** Binding ids in the layout (placed or hidden) that the schema lacks. */
	stale: string[];
	/** Schema controls the user explicitly hid. */
	hidden: ControlDescriptor[];
}

/** Where a binding currently lives in a doc. `index` -1 is the section's header toggle. */
export interface PanelTarget {
	row: string;
	column: string;
}
export type ItemLocation = { section: string; index: number; row?: string; column?: string };
export type Location = ItemLocation | 'hidden' | null;

/** All leaves in row/column/item order; never includes the header toggle. */
export function sectionItems(section: LayoutSection): LayoutItem[] {
	return section.mode === 'panels'
		? section.rows.flatMap((row) => row.columns.flatMap((column) => column.items))
		: section.items;
}

/** Apply an immutable transformation to each leaf list, preserving panel structure. */
export function mapSectionItems(
	section: LayoutSection,
	fn: (items: LayoutItem[]) => LayoutItem[]
): LayoutSection {
	return section.mode === 'panels'
		? {
				...section,
				rows: section.rows.map((row) => ({
					...row,
					columns: row.columns.map((column) => ({ ...column, items: fn(column.items) }))
				}))
			}
		: { ...section, items: fn(section.items) };
}

/** Normalize a valid legacy snapshot without changing order or bindings. */
export function normalizeLayout(doc: LayoutDoc): LayoutDoc {
	return {
		...doc,
		version: 2,
		sections: doc.sections.map((s) => (s.mode === 'panels' ? s : { ...s, mode: 'auto' }))
	};
}

function targetItems(section: LayoutSection, target?: PanelTarget): LayoutItem[] | undefined {
	if (section.mode !== 'panels') return target ? undefined : section.items;
	return target
		? section.rows.find((r) => r.id === target.row)?.columns.find((c) => c.id === target.column)
				?.items
		: section.rows[0]?.columns[0]?.items;
}

function insertItem(
	section: LayoutSection,
	item: LayoutItem,
	index?: number,
	target?: PanelTarget
): LayoutSection {
	const list = targetItems(section, target);
	return mapSectionItems(section, (items) => {
		if (items !== list) return items;
		const next = [...items];
		next.splice(clamp(index ?? next.length, 0, next.length), 0, item);
		return next;
	});
}

function panelTarget(where: ItemLocation): PanelTarget | undefined {
	return where.row && where.column ? { row: where.row, column: where.column } : undefined;
}

export function itemBindings(item: LayoutItem): string[] {
	return item.kind === 'control' ? [item.binding_id] : [item.width, item.height];
}

export function itemId(item: LayoutItem): string {
	return itemBindings(item)[0];
}

function titleCase(name: string): string {
	return name.charAt(0).toUpperCase() + name.slice(1);
}

function byOrder(a: ControlDescriptor, b: ControlDescriptor): number {
	return (
		a.order - b.order || (a.binding_id < b.binding_id ? -1 : a.binding_id > b.binding_id ? 1 : 0)
	);
}

/** The automatic layout: one section per non-empty group, in GROUP_ORDER. Never persisted until saved. */
export function defaultLayout(schema: SchemaLike): LayoutDoc {
	const known = new Set<string>(GROUP_ORDER);
	// Unknown groups (a newer backend) go last rather than vanishing.
	const extra = [...new Set(schema.controls.map((c) => c.group as string))].filter(
		(g) => !known.has(g)
	);
	const sections: LayoutSection[] = [];
	for (const group of [...GROUP_ORDER, ...extra]) {
		const controls = schema.controls.filter((c) => c.group === group).sort(byOrder);
		if (controls.length === 0) continue;
		sections.push({
			mode: 'auto',
			id: group,
			title: titleCase(group),
			columns: group === 'prompts' ? 1 : 2,
			collapsed: COLLAPSED_GROUPS.has(group),
			items: controls.map((c) => ({
				kind: 'control',
				binding_id: c.binding_id,
				span: c.component === 'textarea' ? 'full' : 'auto'
			}))
		});
	}
	return { version: 2, sections, hidden: [] };
}

/** Join a layout (or the automatic one when null) with the schema. */
export function resolveLayout(schema: SchemaLike, layout: LayoutDoc | null): ResolvedLayout {
	const doc = layout ?? defaultLayout(schema);
	const byId = new Map(schema.controls.map((c) => [c.binding_id, c]));
	const seen = new Set<string>();
	const stale: string[] = [];
	const note = (id: string): void => {
		if (!byId.has(id) && !stale.includes(id)) stale.push(id);
	};
	const boolean = (id: string | null | undefined): ControlDescriptor | undefined => {
		const control = id ? byId.get(id) : undefined;
		return control?.logical_type === 'boolean' ? control : undefined;
	};
	const resolveItems = (items: LayoutItem[]): ResolvedControl[] => {
		const controls: ResolvedControl[] = [];
		for (const item of items) {
			const ids = itemBindings(item);
			ids.forEach((id) => {
				seen.add(id);
				note(id);
			});
			const control = byId.get(ids[0]);
			const height = item.kind === 'aspect_ratio' ? byId.get(item.height) : undefined;
			const when = boolean(item.when);
			if (control && (item.kind === 'control' || height))
				controls.push({ item, control, height, when });
			else if (item.kind === 'aspect_ratio') {
				// A stale half must not remove the surviving input from the run page.
				for (const id of ids) {
					const surviving = byId.get(id);
					if (surviving)
						controls.push({
							item: { kind: 'control', binding_id: id, span: item.span },
							control: surviving,
							when
						});
				}
			}
		}
		return controls;
	};
	const sections = doc.sections.map((section): ResolvedSection => {
		if (section.toggle) {
			seen.add(section.toggle);
			note(section.toggle);
		}
		const toggle = boolean(section.toggle);
		if (section.mode !== 'panels')
			return { section, controls: resolveItems(section.items), toggle };
		const rows = section.rows.map((row) => ({
			id: row.id,
			columns: row.columns.map((column) => ({
				id: column.id,
				controls: resolveItems(column.items)
			}))
		}));
		return {
			section,
			controls: rows.flatMap((r) => r.columns.flatMap((c) => c.controls)),
			toggle,
			rows
		};
	});
	const hidden: ControlDescriptor[] = [];
	for (const id of doc.hidden) {
		seen.add(id);
		const control = byId.get(id);
		if (control) hidden.push(control);
		else note(id);
	}
	const unplaced = schema.controls.filter((c) => !seen.has(c.binding_id)).sort(byOrder);
	return { sections, unplaced, stale, hidden };
}

// --- validation -----------------------------------------------------------

type Loose = Record<string, unknown>;

function isObject(value: unknown): value is Loose {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validBinding(value: unknown): boolean {
	return typeof value === 'string' && value.length >= 1 && value.length <= 200;
}

/** Every invariant of v1/v2 documents, mirroring the backend. Empty array = valid. */
export function validate(doc: unknown): string[] {
	const errors: string[] = [];
	if (!isObject(doc)) return ['layout must be an object'];
	if (Object.keys(doc).some((key) => !['version', 'sections', 'hidden'].includes(key)))
		errors.push('unknown layout field');
	if (doc.version !== 1 && doc.version !== 2) errors.push('version must be 1 or 2');
	if (!Array.isArray(doc.sections)) errors.push('sections must be an array');
	if (!Array.isArray(doc.hidden)) errors.push('hidden must be an array');
	if (errors.length) return errors;

	const sections = doc.sections as unknown[];
	const hidden = doc.hidden as unknown[];
	if (sections.length > MAX_SECTIONS) errors.push(`at most ${MAX_SECTIONS} sections`);

	const sectionIds = new Set<string>();
	const structuralIds = new Set<string>();
	const structuralId = (id: unknown, where: string): void => {
		if (typeof id !== 'string' || !SECTION_ID.test(id))
			errors.push(`${where}: id must match ${SECTION_ID.source}`);
		else if (structuralIds.has(id)) errors.push(`${where}: duplicate structural id "${id}"`);
		else structuralIds.add(id);
	};
	const bindings = new Set<string>();
	let total = hidden.length;
	const claim = (id: unknown, where: string): void => {
		if (!validBinding(id)) {
			errors.push(`${where}: binding_id must be 1-200 characters`);
			return;
		}
		if (bindings.has(id as string)) errors.push(`${where}: binding "${id}" appears more than once`);
		bindings.add(id as string);
	};

	sections.forEach((raw, index) => {
		const where = `section ${index + 1}`;
		if (!isObject(raw)) {
			errors.push(`${where}: must be an object`);
			return;
		}
		const sectionFields = [
			'id',
			'title',
			'mode',
			'collapsed',
			'toggle',
			...(raw.mode === 'panels' ? ['rows'] : ['columns', 'items'])
		];
		if (Object.keys(raw).some((key) => !sectionFields.includes(key)))
			errors.push(`${where}: unknown section field`);
		if (typeof raw.id !== 'string' || !SECTION_ID.test(raw.id)) {
			errors.push(`${where}: id must match ${SECTION_ID.source}`);
		} else if (sectionIds.has(raw.id)) {
			errors.push(`${where}: duplicate section id "${raw.id}"`);
		} else {
			sectionIds.add(raw.id);
		}
		const title = typeof raw.title === 'string' ? raw.title.trim() : '';
		if (title.length < 1 || title.length > MAX_TITLE) {
			errors.push(`${where}: title must be 1-${MAX_TITLE} characters`);
		}
		if (raw.mode !== 'panels' && raw.columns !== 1 && raw.columns !== 2 && raw.columns !== 3) {
			errors.push(`${where}: columns must be 1, 2, or 3`);
		}
		if (typeof raw.collapsed !== 'boolean') errors.push(`${where}: collapsed must be a boolean`);
		if (raw.toggle != null) {
			total += 1;
			claim(raw.toggle, `${where} toggle`);
		}
		let leaves: unknown[] = [];
		if (doc.version === 1 && ('mode' in raw || 'rows' in raw))
			errors.push(`${where}: v1 sections must use columns and items`);
		if (raw.mode === 'panels') {
			if ('items' in raw || 'columns' in raw)
				errors.push(`${where}: panels cannot have section items/columns`);
			if (!Array.isArray(raw.rows)) errors.push(`${where}: rows must be an array`);
			else {
				if (raw.rows.length > MAX_ROWS) errors.push(`${where}: at most ${MAX_ROWS} rows`);
				for (const row of raw.rows) {
					if (!isObject(row)) {
						errors.push(`${where}: row must be an object`);
						continue;
					}
					structuralId(row.id, 'row');
					if (Object.keys(row).some((key) => !['id', 'columns'].includes(key)))
						errors.push(`${where}: unknown row field`);
					if (
						!Array.isArray(row.columns) ||
						row.columns.length < 1 ||
						row.columns.length > MAX_COLUMNS
					) {
						errors.push(`${where}: row needs 1-${MAX_COLUMNS} columns`);
						continue;
					}
					for (const column of row.columns) {
						if (!isObject(column)) {
							errors.push(`${where}: column must be an object`);
							continue;
						}
						structuralId(column.id, 'column');
						if (Object.keys(column).some((key) => !['id', 'items'].includes(key)))
							errors.push(`${where}: unknown column field`);
						if (!Array.isArray(column.items))
							errors.push(`${where}: column items must be an array`);
						else leaves.push(...column.items);
					}
				}
			}
		} else if (raw.mode !== undefined && raw.mode !== 'auto')
			errors.push(`${where}: mode must be auto or panels`);
		else if ('rows' in raw) errors.push(`${where}: auto cannot have rows`);
		else if (Array.isArray(raw.items)) leaves = raw.items;
		if (raw.mode !== 'panels' && !Array.isArray(raw.items)) {
			errors.push(`${where}: items must be an array`);
			return;
		}
		for (const item of leaves) {
			if (!isObject(item)) {
				errors.push(`${where}: item must be an object`);
				continue;
			}
			if (item.kind !== 'control' && item.kind !== 'aspect_ratio')
				errors.push(`${where}: unknown item kind`);
			const fields =
				item.kind === 'aspect_ratio'
					? ['kind', 'width', 'height', 'presets', 'span', 'when']
					: ['kind', 'binding_id', 'span', 'when'];
			if (Object.keys(item).some((key) => !fields.includes(key)))
				errors.push(`${where}: unknown item field`);
			if (item.when != null && !validBinding(item.when))
				errors.push(`${where}: when must be 1-200 characters`);
			if (item.span !== undefined && item.span !== 'auto' && item.span !== 'full') {
				errors.push(`${where}: span must be "auto" or "full"`);
			}
			if (item.kind === 'aspect_ratio') {
				total += 2;
				claim(item.width, where);
				claim(item.height, where);
				if (
					item.presets != null &&
					(!Array.isArray(item.presets) ||
						item.presets.length > 24 ||
						!item.presets.every(
							(pair) =>
								Array.isArray(pair) &&
								pair.length === 2 &&
								pair.every((n) => Number.isInteger(n) && n > 0 && n <= 16384)
						))
				)
					errors.push(`${where}: invalid dimension presets`);
			} else {
				total += 1;
				claim(item.binding_id, where);
			}
		}
	});
	for (const id of hidden) claim(id, 'hidden');
	if (total > MAX_ITEMS) errors.push(`at most ${MAX_ITEMS} items including hidden`);
	return errors;
}

/** A fresh section id not present in `existing`; always matches the id pattern. */
export function newSectionId(existing: Iterable<string>): string {
	const taken = new Set(existing);
	for (;;) {
		const id = `s${Math.random().toString(36).slice(2, 10)}`;
		if (id.length >= 3 && !taken.has(id)) return id;
	}
}

// --- operations -----------------------------------------------------------

function cleanTitle(title: string): string {
	return title.trim().slice(0, MAX_TITLE).trim();
}

function clamp(value: number, low: number, high: number): number {
	return Math.max(low, Math.min(high, value));
}

/** Placed plus hidden bindings: what the backend caps at MAX_ITEMS. */
export function itemCount(doc: LayoutDoc): number {
	return doc.sections.reduce(
		(n, s) =>
			n +
			(s.toggle ? 1 : 0) +
			sectionItems(s).reduce((sum, item) => sum + itemBindings(item).length, 0),
		doc.hidden.length
	);
}

export function canAddSection(doc: LayoutDoc): boolean {
	return doc.sections.length < MAX_SECTIONS;
}

/** False only when `binding` is not in the doc yet and the item cap is reached. */
export function canPlace(doc: LayoutDoc, binding: string): boolean {
	return itemCount(doc) < MAX_ITEMS || locate(doc, binding) !== null;
}

/** Where `binding` lives: a section + index, `'hidden'`, or null when unplaced/absent. */
export function locate(doc: LayoutDoc, binding: string): Location {
	for (const section of doc.sections) {
		if (section.toggle === binding) return { section: section.id, index: -1 };
		if (section.mode === 'panels') {
			for (const row of section.rows)
				for (const column of row.columns) {
					const index = column.items.findIndex((item) => itemBindings(item).includes(binding));
					if (index >= 0) return { section: section.id, row: row.id, column: column.id, index };
				}
		} else {
			const index = section.items.findIndex((item) => itemBindings(item).includes(binding));
			if (index >= 0) return { section: section.id, index };
		}
	}
	return doc.hidden.includes(binding) ? 'hidden' : null;
}

/** The doc without `binding` anywhere, plus the removed item (to keep its span). */
function strip(doc: LayoutDoc, binding: string): { doc: LayoutDoc; item: LayoutItem | null } {
	let item: LayoutItem | null = null;
	const sections = doc.sections.map((section) => {
		if (section.toggle === binding) return { ...section, toggle: null };
		const found = sectionItems(section).find((it) => itemBindings(it).includes(binding));
		if (!found) return section;
		item = found;
		return mapSectionItems(section, (items) =>
			items.filter((it) => !itemBindings(it).includes(binding))
		);
	});
	return { doc: { ...doc, sections, hidden: doc.hidden.filter((id) => id !== binding) }, item };
}

function mapSection(
	doc: LayoutDoc,
	id: string,
	fn: (section: LayoutSection) => LayoutSection
): LayoutDoc {
	if (!doc.sections.some((s) => s.id === id)) return doc;
	return { ...doc, sections: doc.sections.map((s) => (s.id === id ? fn(s) : s)) };
}

/**
 * Insert a new empty section (default: at the end). Pass `id` when the caller
 * needs to know it up front (to select the new section); otherwise one is generated.
 */
export function addSection(
	doc: LayoutDoc,
	title: string,
	index?: number,
	id: string = newSectionId(doc.sections.map((s) => s.id))
): LayoutDoc {
	if (!canAddSection(doc) || !SECTION_ID.test(id) || doc.sections.some((s) => s.id === id))
		return doc;
	const section: LayoutSection = {
		mode: 'auto',
		id,
		title: cleanTitle(title) || 'Section',
		columns: 1,
		collapsed: false,
		items: []
	};
	const at = clamp(index ?? doc.sections.length, 0, doc.sections.length);
	const sections = [...doc.sections];
	sections.splice(at, 0, section);
	return normalizeLayout({ ...doc, sections });
}

/** Delete a section; its controls become unplaced (never hidden). */
export function removeSection(doc: LayoutDoc, id: string): LayoutDoc {
	if (!doc.sections.some((s) => s.id === id)) return doc;
	return { ...doc, sections: doc.sections.filter((s) => s.id !== id) };
}

export function updateSection(
	doc: LayoutDoc,
	id: string,
	patch: { title?: string; columns?: 1 | 2 | 3; collapsed?: boolean }
): LayoutDoc {
	return mapSection(doc, id, (section) => ({
		...section,
		...(section.mode === 'panels' ? { collapsed: patch.collapsed ?? section.collapsed } : patch),
		title: patch.title === undefined ? section.title : cleanTitle(patch.title) || section.title
	}));
}

export function renameSection(doc: LayoutDoc, id: string, title: string): LayoutDoc {
	return updateSection(doc, id, { title });
}

/** Reorder sections: take the one at index `from` and put it at index `to` (final position). */
export function moveSection(doc: LayoutDoc, from: number, to: number): LayoutDoc {
	if (from < 0 || from >= doc.sections.length) return doc;
	const target = clamp(to, 0, doc.sections.length - 1);
	if (target === from) return doc;
	const sections = [...doc.sections];
	const [moved] = sections.splice(from, 1);
	sections.splice(target, 0, moved);
	return { ...doc, sections };
}

/**
 * Put a control into a section, removing it from wherever it was (another
 * section or hidden). `index` is the final position in the section (default:
 * end). A control that was already placed keeps its span.
 */
export function placeControl(
	doc: LayoutDoc,
	binding: string,
	sectionId: string,
	index?: number,
	target?: PanelTarget
): LayoutDoc {
	const destination = doc.sections.find((s) => s.id === sectionId);
	if (!destination || !targetItems(destination, target) || !canPlace(doc, binding)) return doc;
	const { doc: stripped, item } = strip(doc, binding);
	const placed: LayoutItem = item ?? { kind: 'control', binding_id: binding, span: 'auto' };
	return mapSection(stripped, sectionId, (section) => insertItem(section, placed, index, target));
}

/** Move an already-placed control (reorder or cross-section); no-op if it is not in a section. */
export function moveItem(
	doc: LayoutDoc,
	binding: string,
	toSectionId: string,
	toIndex?: number,
	target?: PanelTarget
): LayoutDoc {
	const where = locate(doc, binding);
	if (where === null || where === 'hidden') return doc;
	return placeControl(doc, binding, toSectionId, toIndex, target);
}

/** Replace two separate controls with one paired item at the width's position. */
export function pairDimensions(doc: LayoutDoc, width: string, height: string): LayoutDoc {
	const where = locate(doc, width);
	if (width === height || !where || where === 'hidden') return doc;
	if (
		![width, height].every((id) =>
			doc.sections.some((s) =>
				sectionItems(s).some((item) => item.kind === 'control' && item.binding_id === id)
			)
		)
	)
		return doc;
	const target = panelTarget(where);
	const index = targetItems(
		doc.sections.find((s) => s.id === where.section)!,
		target
	)!
		.slice(0, where.index)
		.filter((item) => !itemBindings(item).includes(height)).length;
	const stripped = strip(strip(doc, width).doc, height).doc;
	const next = mapSection(stripped, where.section, (section) =>
		insertItem(section, { kind: 'aspect_ratio', width, height, span: 'full' }, index, target)
	);
	return itemCount(next) <= MAX_ITEMS ? next : doc;
}

export function splitDimensions(doc: LayoutDoc, binding: string): LayoutDoc {
	const where = locate(doc, binding);
	if (!where || where === 'hidden') return doc;
	return mapSection(doc, where.section, (section) =>
		mapSectionItems(section, (items) =>
			items.flatMap((item): LayoutItem[] =>
				item.kind === 'aspect_ratio' && itemBindings(item).includes(binding)
					? itemBindings(item).map((binding_id) => ({ kind: 'control', binding_id, span: 'auto' }))
					: [item]
			)
		)
	);
}

/** Remove a control from the layout entirely; it becomes unplaced. */
export function unplace(doc: LayoutDoc, binding: string): LayoutDoc {
	return locate(doc, binding) === null ? doc : strip(doc, binding).doc;
}

/** Explicitly hide a control (never rendered; imported value still submitted). */
export function hide(doc: LayoutDoc, binding: string): LayoutDoc {
	if (locate(doc, binding) === 'hidden' || !canPlace(doc, binding)) return doc;
	const { doc: stripped } = strip(doc, binding);
	const original = doc.sections
		.flatMap(sectionItems)
		.find((item) => itemBindings(item).includes(binding));
	return {
		...stripped,
		hidden: [...stripped.hidden, ...(original ? itemBindings(original) : [binding])]
	};
}

/** Undo `hide`: the control becomes unplaced. */
export function unhide(doc: LayoutDoc, binding: string): LayoutDoc {
	return doc.hidden.includes(binding)
		? { ...doc, hidden: doc.hidden.filter((id) => id !== binding) }
		: doc;
}

export function setSpan(doc: LayoutDoc, binding: string, span: 'auto' | 'full'): LayoutDoc {
	const where = locate(doc, binding);
	if (where === null || where === 'hidden') return doc;
	return mapSection(doc, where.section, (section) =>
		mapSectionItems(section, (items) =>
			items.map((item) => (itemBindings(item).includes(binding) ? { ...item, span } : item))
		)
	);
}

/** Drop a stale binding (absent from the schema) wherever it is kept. */
export function removeStale(doc: LayoutDoc, binding: string): LayoutDoc {
	return unplace(doc, binding);
}

/** Make `binding` the section's header switch (moving it from wherever it was), or clear it with null. */
export function setToggle(doc: LayoutDoc, sectionId: string, binding: string | null): LayoutDoc {
	const section = doc.sections.find((s) => s.id === sectionId);
	if (!section || (section.toggle ?? null) === binding) return doc;
	if (binding === null) return mapSection(doc, sectionId, (s) => ({ ...s, toggle: null }));
	if (!canPlace(doc, binding)) return doc;
	return mapSection(strip(doc, binding).doc, sectionId, (s) => ({ ...s, toggle: binding }));
}

/** Gate a placed item on a boolean binding (null removes the condition). */
export function setWhen(doc: LayoutDoc, binding: string, when: string | null): LayoutDoc {
	const where = locate(doc, binding);
	if (where === null || where === 'hidden' || where.index < 0 || when === binding) return doc;
	return mapSection(doc, where.section, (section) =>
		mapSectionItems(section, (items) =>
			items.map((item) => (itemBindings(item).includes(binding) ? { ...item, when } : item))
		)
	);
}

// --- panel structure --------------------------------------------------------

/** Row and column ids share one document-wide namespace. */
function newStructuralId(doc: LayoutDoc, extra: string[] = []): string {
	const taken = doc.sections.flatMap((s) =>
		s.mode === 'panels' ? s.rows.flatMap((r) => [r.id, ...r.columns.map((c) => c.id)]) : []
	);
	return newSectionId([...taken, ...extra]);
}

function emptyRow(doc: LayoutDoc, columns: number): LayoutRow {
	const ids: string[] = [];
	for (let i = 0; i <= columns; i++) ids.push(newStructuralId(doc, ids));
	return { id: ids[0], columns: ids.slice(1).map((id) => ({ id, items: [] })) };
}

function mapPanels(
	doc: LayoutDoc,
	id: string,
	fn: (section: PanelLayoutSection) => PanelLayoutSection
): LayoutDoc {
	const section = doc.sections.find((s) => s.id === id);
	if (section?.mode !== 'panels') return doc;
	const next = fn(section);
	return next === section ? doc : mapSection(doc, id, () => next);
}

/**
 * Switch a section between auto flow and panels without losing controls.
 * To panels: one row with the section's column count, items dealt round-robin
 * (close to the grid's reading order). To auto: leaves flattened in row/column order.
 */
export function setSectionMode(doc: LayoutDoc, id: string, mode: 'auto' | 'panels'): LayoutDoc {
	const section = doc.sections.find((s) => s.id === id);
	if (!section || (section.mode ?? 'auto') === mode) return doc;
	if (section.mode === 'panels') {
		const columns = clamp(
			Math.max(1, ...section.rows.map((r) => r.columns.length)),
			1,
			MAX_COLUMNS
		) as 1 | 2 | 3;
		const { rows: _rows, ...base } = section;
		return mapSection(doc, id, () => ({
			...base,
			mode: 'auto',
			columns,
			items: sectionItems(section)
		}));
	}
	const row = emptyRow(doc, section.columns);
	section.items.forEach((item, i) => row.columns[i % row.columns.length].items.push(item));
	const { items: _items, columns: _columns, ...base } = section;
	return mapSection(doc, id, () => ({ ...base, mode: 'panels', rows: [row] }));
}

/** Append (or insert at `index`) an empty row of `columns` equal columns. */
export function addRow(doc: LayoutDoc, sectionId: string, columns = 1, index?: number): LayoutDoc {
	return mapPanels(doc, sectionId, (section) => {
		if (section.rows.length >= MAX_ROWS) return section;
		const rows = [...section.rows];
		rows.splice(
			clamp(index ?? rows.length, 0, rows.length),
			0,
			emptyRow(doc, clamp(columns, 1, MAX_COLUMNS))
		);
		return { ...section, rows };
	});
}

/** Delete a row; its controls become unplaced. */
export function removeRow(doc: LayoutDoc, sectionId: string, rowId: string): LayoutDoc {
	return mapPanels(doc, sectionId, (section) => ({
		...section,
		rows: section.rows.filter((r) => r.id !== rowId)
	}));
}

/** Move a row up (-1) or down (+1) within its section. */
export function shiftRow(
	doc: LayoutDoc,
	sectionId: string,
	rowId: string,
	delta: number
): LayoutDoc {
	return mapPanels(doc, sectionId, (section) => {
		const from = section.rows.findIndex((r) => r.id === rowId);
		const to = from + delta;
		if (from < 0 || to < 0 || to >= section.rows.length) return section;
		const rows = [...section.rows];
		const [row] = rows.splice(from, 1);
		rows.splice(to, 0, row);
		return { ...section, rows };
	});
}

/** Resize a row to `count` columns. Shrinking moves the dropped columns' controls into the last kept one. */
export function setRowColumns(
	doc: LayoutDoc,
	sectionId: string,
	rowId: string,
	count: number
): LayoutDoc {
	const n = clamp(count, 1, MAX_COLUMNS);
	return mapPanels(doc, sectionId, (section) => ({
		...section,
		rows: section.rows.map((row) => {
			if (row.id !== rowId || row.columns.length === n) return row;
			if (row.columns.length > n) {
				const kept = row.columns.slice(0, n);
				const moved = row.columns.slice(n).flatMap((c) => c.items);
				return {
					...row,
					columns: kept.map((c, i) => (i === n - 1 ? { ...c, items: [...c.items, ...moved] } : c))
				};
			}
			const ids: string[] = [];
			while (row.columns.length + ids.length < n) ids.push(newStructuralId(doc, ids));
			return { ...row, columns: [...row.columns, ...ids.map((id) => ({ id, items: [] }))] };
		})
	}));
}

/** Delete one column; its controls become unplaced. The last column of a row deletes the row. */
export function removeColumn(
	doc: LayoutDoc,
	sectionId: string,
	rowId: string,
	columnId: string
): LayoutDoc {
	return mapPanels(doc, sectionId, (section) => ({
		...section,
		rows: section.rows.flatMap((row) => {
			if (row.id !== rowId) return [row];
			const columns = row.columns.filter((c) => c.id !== columnId);
			return columns.length ? [{ ...row, columns }] : [];
		})
	}));
}
