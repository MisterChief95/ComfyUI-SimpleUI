// Pure helpers for the per-control presentation drafts of the designer (label,
// widget, display range, help text). No Svelte, no DOM: node runs it
// (`npm test`), so keep to erasable TypeScript and `import type`.
//
// A draft is *sparse*: a patch holding only the fields the user changed from
// the control's effective descriptor (the schema already has saved corrections
// replayed over it). Saving sends the saved presentation plus that patch.
import type { Component, ControlDescriptor, Presentation } from '../contracts.ts';

export interface Draft {
	label: string;
	component: Component;
	help_text: string;
	display_min: string;
	display_max: string;
	display_step: string;
	display_default: string;
}

export type DraftPatch = Partial<Draft>;

export const DISPLAY_KEYS = ['display_min', 'display_max', 'display_step', 'display_default'] as const;

/** An adjustable numeric range exists (exact ints/seeds never have one). */
export function isRangedNumber(control: ControlDescriptor): boolean {
	return (
		(control.logical_type === 'int' || control.logical_type === 'float') &&
		control.constraints !== null &&
		control.constraints.exact_min === null &&
		control.constraints.exact_max === null
	);
}

function text(value: unknown): string {
	return value === null || value === undefined ? '' : String(value);
}

/** The draft that equals the descriptor as it is today (nothing changed). */
export function baselineDraft(control: ControlDescriptor): Draft {
	const ranged = isRangedNumber(control);
	const c = control.constraints;
	return {
		label: control.label,
		component: control.component,
		help_text: control.help_text ?? '',
		display_min: ranged ? text(c?.min) : '',
		display_max: ranged ? text(c?.max) : '',
		display_step: ranged ? text(c?.step) : '',
		display_default: ranged ? text(control.value) : ''
	};
}

export function effectiveDraft(control: ControlDescriptor, patch: DraftPatch | undefined): Draft {
	return { ...baselineDraft(control), ...patch };
}

/** `patch` without the fields that equal the baseline, so "dirty" means a real change. */
export function normalizePatch(control: ControlDescriptor, patch: DraftPatch): DraftPatch {
	const base = baselineDraft(control);
	const out: Record<string, string> = {};
	for (const [key, value] of Object.entries(patch) as [keyof Draft, string][]) {
		const baseValue = base[key];
		const same =
			key === 'label' || key === 'help_text' ? value.trim() === baseValue.trim() : value === baseValue;
		if (!same) out[key] = value;
	}
	return out as DraftPatch;
}

/** A finite number from user text, or null for blank/garbage. */
export function parseNumber(value: string | undefined): number | null {
	if (value === undefined || value.trim() === '') return null;
	const n = Number(value);
	return Number.isFinite(n) ? n : null;
}

/** The descriptor as the draft would show it, for the live preview. */
export function applyDraft(control: ControlDescriptor, patch: DraftPatch | undefined): ControlDescriptor {
	if (!patch || Object.keys(patch).length === 0) return control;
	const next: ControlDescriptor = { ...control };
	if (patch.label !== undefined && patch.label.trim()) next.label = patch.label.trim();
	if (patch.help_text !== undefined) next.help_text = patch.help_text.trim() || null;
	if (patch.component !== undefined) next.component = patch.component;
	if (isRangedNumber(control) && control.constraints) {
		next.constraints = {
			...control.constraints,
			min: parseNumber(patch.display_min) ?? control.constraints.min,
			max: parseNumber(patch.display_max) ?? control.constraints.max,
			step: parseNumber(patch.display_step) ?? control.constraints.step
		};
		const fallback = parseNumber(patch.display_default);
		if (fallback !== null) {
			next.value = control.logical_type === 'int' ? String(Math.trunc(fallback)) : fallback;
		}
	}
	return next;
}

export function emptyPresentation(): Presentation {
	return {
		label: null,
		group: null,
		order: null,
		help_text: null,
		component: null,
		display_min: null,
		display_max: null,
		display_default: null,
		display_step: null
	};
}

/** The PUT body's presentation: the saved one (legacy group/order kept) plus the patch. */
export function buildPresentation(saved: Presentation | null, patch: DraftPatch): Presentation {
	const out: Presentation = { ...(saved ?? emptyPresentation()) };
	if (patch.label !== undefined && patch.label.trim()) out.label = patch.label.trim();
	if (patch.component !== undefined) out.component = patch.component;
	if (patch.help_text !== undefined) out.help_text = patch.help_text.trim() || null;
	for (const key of DISPLAY_KEYS) {
		if (patch[key] !== undefined) out[key] = parseNumber(patch[key]);
	}
	return out;
}
