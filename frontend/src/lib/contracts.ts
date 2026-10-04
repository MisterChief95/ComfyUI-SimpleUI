// Mirror of backend/app/contracts.py. Change both files together.
//
// ExactInt values are decimal strings on purpose: JavaScript numbers lose
// precision above 2**53, and seeds/IDs must round-trip exactly. Never
// Number(...) an ExactInt for anything but display width; use BigInt if you
// must do arithmetic, and send the string back unchanged.

/** A signed integer of arbitrary magnitude, as a decimal string. */
export type ExactInt = string;
export type Id = string;

export const EXACT_INT = /^-?(0|[1-9][0-9]*)$/;

export function isExactInt(value: string): boolean {
	return EXACT_INT.test(value);
}

export type ErrorCode =
	| 'bad_request'
	| 'unauthorized'
	| 'forbidden'
	| 'not_found'
	| 'conflict'
	| 'unprocessable'
	| 'rate_limited'
	| 'upstream_unavailable'
	| 'internal';

export interface ErrorDetail {
	field: string | null;
	code: string;
	message: string;
}

export interface ApiError {
	code: ErrorCode;
	message: string;
	request_id: string;
	details: ErrorDetail[];
}

export interface ErrorEnvelope {
	error: ApiError;
}

/** Cursor pagination. `next_cursor === null` means the end of the list. */
export interface Page<T> {
	items: T[];
	next_cursor: string | null;
}

/** Response-only ownership stamp; the server derives it from the session. */
export interface Owned {
	owner_id: Id;
}

export interface Revisioned {
	revision: number;
}

export type LogicalType = 'string' | 'int' | 'float' | 'boolean' | 'enum' | 'file' | 'unknown';

export type Component =
	'text' | 'textarea' | 'number' | 'slider' | 'checkbox' | 'select' | 'seed' | 'file' | 'readonly';

export type Group =
	| 'prompts'
	| 'model'
	| 'generation'
	| 'dimensions'
	| 'inputs'
	| 'video'
	| 'advanced'
	| 'output'
	| 'inactive';

export interface NumberConstraints {
	min: number | null;
	max: number | null;
	step: number | null;
	exact_min: ExactInt | null;
	exact_max: ExactInt | null;
}

export interface EnumOption {
	/** In the JSON type ComfyUI listed it with; send it back unchanged. */
	value: string | number | boolean;
	label: string;
	available: boolean;
}

/**
 * One editable literal in an imported graph. `value` is encoded by
 * `logical_type`: ExactInt string for `int`, number for `float`, boolean for
 * `boolean`, string for everything else.
 */
export interface ControlDescriptor {
	binding_id: Id;
	node_id: string;
	class_type: string;
	input_name: string;
	logical_type: LogicalType;
	value: string | boolean | number | null;
	component: Component;
	group: Group;
	order: number;
	label: string;
	help_text: string | null;
	constraints: NumberConstraints | null;
	options: EnumOption[] | null;
	multiline: boolean;
	inference_reason: string;
	unresolved: ErrorDetail[];
	raw_metadata: Record<string, unknown> | null;
}

export interface ControlSchema extends Owned, Revisioned {
	workflow_id: Id;
	controls: ControlDescriptor[];
	blocking: ErrorDetail[];
	warnings: ErrorDetail[];
}

export interface Health {
	status: 'ok';
	version: string;
	time_ms: ExactInt;
}

// --- auth/routes.py --------------------------------------------------------

export interface ProfileInfo {
	id: Id;
	name: string;
	is_default: boolean;
}

export interface SessionInfo {
	authenticated: boolean;
	multi_user: boolean;
	/** True for the implicit single-user Default session (no cookie). */
	anonymous: boolean;
	profile: ProfileInfo | null;
	/** Send back as X-CSRF-Token on mutations. Null for the implicit session. */
	csrf_token: string | null;
}

// --- settings/routes.py -----------------------------------------------------

/** Mirrors backend Value = bool | int | str in settings/routes.py. */
export type SettingValue = boolean | number | string;

export interface EffectiveSettings {
	host: Record<string, SettingValue>;
	profile: Record<string, SettingValue>;
	/** True when this request could change host settings (it is local). */
	host_writable: boolean;
	multi_user: boolean;
}

// --- workflows/routes.py ----------------------------------------------------

export interface WorkflowInfo {
	id: Id;
	name: string;
	current_revision: number;
	updated_ms: ExactInt;
}

/** PUT /api/workflows/{id}/graph. `added`/`removed` are control binding ids. */
export interface GraphReplaceResult {
	workflow: WorkflowInfo;
	revision: number;
	added: string[];
	removed: string[];
}

/** Named snapshot of control values (docs/UI_DESIGNER.md "Presets"). Int values may be numbers or ExactInt strings. */
export interface Preset {
	id: Id;
	name: string;
	values: Record<string, string | boolean | number>;
	revision: number;
	created_ms: ExactInt;
	updated_ms: ExactInt;
}

/** Reusable prompt snippets (app/styles.py); `{prompt}` in `positive` marks where the user's text goes. */
export interface Style {
	id: Id;
	name: string;
	positive: string;
	negative: string;
	revision: number;
	created_ms: ExactInt;
	updated_ms: ExactInt;
}

// --- mapping/corrections.py --------------------------------------------------

export type CorrectionScope = 'workflow' | 'node_class';

/** Everything a saved correction may change -- presentation only. */
export interface Presentation {
	label: string | null;
	group: Group | null;
	order: number | null;
	help_text: string | null;
	component: Component | null;
	display_min: number | null;
	display_max: number | null;
	display_default: number | null;
	display_step: number | null;
}

export interface Correction {
	scope: CorrectionScope;
	selector: string;
	schema_signature: string;
	presentation: Presentation;
	revision: number;
	stale: boolean;
}

/** Components a correction may choose, per logical type (mirrors ALLOWED_COMPONENTS). */
export const ALLOWED_COMPONENTS: Record<LogicalType, Component[]> = {
	string: ['text', 'textarea', 'readonly'],
	int: ['number', 'slider', 'seed', 'readonly'],
	float: ['number', 'slider', 'readonly'],
	boolean: ['checkbox', 'readonly'],
	enum: ['select', 'readonly'],
	file: ['file', 'readonly'],
	unknown: ['readonly']
};

// --- generations/routes.py --------------------------------------------------

export type SeedPolicy = 'fixed' | 'random' | 'increment';

export interface GenerationInfo {
	id: Id;
	workflow_id: Id | null;
	status: string;
	output_state: string;
	error: Record<string, unknown> | null;
	created_ms: ExactInt;
	updated_ms: ExactInt;
	can_retry?: boolean;
}

export interface GenerationDetail extends GenerationInfo {
	effective_values: Record<string, unknown> | null;
}

// --- media/routes.py --------------------------------------------------------

export type MediaKind = 'image' | 'video' | 'other';

export interface MediaInfo {
	id: Id;
	generation_id: Id | null;
	media_kind: MediaKind;
	media_type: string;
	state: string;
	/** SQLite-backed media routes currently serialize this flag as 0/1. */
	favorite: boolean | 0 | 1;
	created_ms: number;
	filename: string;
	collections?: { id: Id; name: string }[];
}

export type MediaPage = Page<MediaInfo>;

export interface ClearHistoryResult {
	purged: number;
	deferred: number;
}

// --- workflows/layout (docs/UI_DESIGNER.md "Layout document (v1)") -----------

/** A value a user can edit/submit: ExactInt string for int, number for float,
 *  real boolean for checkbox, string for the rest. Matches POST /api/generations edits. */
export type EditValue = string | boolean | number;

/** One placed control. The `kind` union is open for later typed composites. */
export interface ControlLayoutItem {
	kind: 'control';
	binding_id: Id;
	/** `auto` = one grid cell, `full` = the whole row. Defaults to `auto`. */
	span?: 'auto' | 'full';
	/** Boolean binding: shown on the run page only while it is true. A reference, not a placement. */
	when?: Id | null;
}

export interface AspectRatioLayoutItem {
	kind: 'aspect_ratio';
	width: Id;
	height: Id;
	presets?: [number, number][] | null;
	span?: 'auto' | 'full';
	when?: Id | null;
}

export type LayoutItem = ControlLayoutItem | AspectRatioLayoutItem;

export interface LayoutSectionBase {
	/** `^[A-Za-z0-9_-]{1,40}$`, unique within the document, client generated. */
	id: string;
	title: string;
	/** Default disclosure state on the run page. */
	collapsed: boolean;
	/** Boolean binding shown as the header switch; the body folds away while false. Places that control. */
	toggle?: Id | null;
}

export interface AutoLayoutSection extends LayoutSectionBase {
	/** Omission means auto; legacy v1 documents omit this field. */
	mode?: 'auto';
	/** Desktop hint only; phones use 1, tablets at most 2. */
	columns: 1 | 2 | 3;
	items: LayoutItem[];
}

export interface LayoutColumn {
	id: string;
	items: LayoutItem[];
}

export interface LayoutRow {
	id: string;
	/** 1–3 equal-width columns. No deeper nesting. */
	columns: LayoutColumn[];
}

export interface PanelLayoutSection extends LayoutSectionBase {
	mode: 'panels';
	/** 0–40 rows; row/column ids are unique across the document. */
	rows: LayoutRow[];
}

export type LayoutSection = AutoLayoutSection | PanelLayoutSection;

export interface LayoutDoc {
	/** Backend reads v1, returns and saves v2. Legacy client snapshots remain readable. */
	version: 1 | 2;
	sections: LayoutSection[];
	/** Binding ids the user explicitly hid. Never rendered, still submitted unchanged. */
	hidden: Id[];
}

export interface WorkflowLayout {
	workflow_id: Id;
	/** 0 with `layout: null` means nothing saved yet. */
	revision: number;
	schema_signature: string;
	layout: LayoutDoc | null;
	/** Saved binding ids absent from the current schema. */
	stale_bindings: Id[];
}

export interface SaveLayout {
	layout: LayoutDoc;
	expected_revision: number;
}
