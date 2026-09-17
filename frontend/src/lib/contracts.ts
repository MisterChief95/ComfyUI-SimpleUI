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
	| 'text'
	| 'textarea'
	| 'number'
	| 'slider'
	| 'checkbox'
	| 'select'
	| 'seed'
	| 'file'
	| 'readonly';

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
	value: string;
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
}

export type MediaPage = Page<MediaInfo>;

export interface ClearHistoryResult {
	purged: number;
	deferred: number;
}
