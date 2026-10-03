// Pure draft-value helpers for the run page (no Svelte, runs under `npm test`).
// Values are compared in their wire encoding: ExactInt strings as strings,
// floats numerically, booleans as booleans, everything else as strings.
import type { ControlDescriptor, EditValue } from '../contracts.ts';
import { inExactRange } from '../controls/exact.ts';

/** The imported value of a control as an EditValue (null becomes false / ''). */
export function baseValue(control: ControlDescriptor): EditValue {
	const value = control.value;
	if (value === null || value === undefined) return control.logical_type === 'boolean' ? false : '';
	return value;
}

function normalize(control: ControlDescriptor, value: EditValue): boolean | number | string | null {
	switch (control.logical_type) {
		case 'boolean':
			return value === true || value === 'true';
		case 'float':
			// An empty or boolean value is "no number"; NaN never equals itself, so
			// unparseable text always counts as modified.
			return value === '' || typeof value === 'boolean' ? null : Number(value);
		case 'enum':
			// Options keep their JSON type: "1" and 1 are different choices.
			return `${typeof value}:${value}`;
		default:
			return String(value);
	}
}

export function sameValue(control: ControlDescriptor, a: EditValue, b: EditValue): boolean {
	return normalize(control, a) === normalize(control, b);
}

/** Coerce a stored `effective_values` entry to the control's edit encoding. */
export function coerceValue(control: ControlDescriptor, value: unknown): EditValue {
	if (control.logical_type === 'boolean') return value === true || value === 'true';
	if (control.logical_type === 'float') return typeof value === 'number' ? value : Number(value);
	if (control.logical_type === 'enum' && ['string', 'number', 'boolean'].includes(typeof value))
		return value as EditValue;
	return value === null || value === undefined ? '' : String(value);
}

const FLOAT = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

function bounds(min: unknown, max: unknown): string {
	return `Must be between ${min ?? '−∞'} and ${max ?? '∞'}`;
}

/**
 * Why `value` cannot be submitted for `control`, or null when it can. Only int
 * and float controls edited as numbers are checked: ExactInt syntax with exact
 * bounds (BigInt, never rounded through Number), and finite floats within min/max.
 */
export function validateValue(control: ControlDescriptor, value: EditValue): string | null {
	const type = control.logical_type;
	if (type !== 'int' && type !== 'float') return null;
	if (control.component === 'select' || control.component === 'readonly') return null;
	const text = value === null || value === undefined ? '' : String(value);
	const c = control.constraints;
	if (type === 'int') {
		if (typeof value === 'boolean') return 'Enter a whole number';
		if (typeof value === 'number' && !Number.isSafeInteger(value))
			return 'Enter a whole number as text to preserve its exact value';
		const min = c?.exact_min ?? (c?.min != null ? String(Math.ceil(c.min)) : null);
		const max = c?.exact_max ?? (c?.max != null ? String(Math.floor(c.max)) : null);
		if (!/^-?(0|[1-9][0-9]*)$/.test(text)) return 'Enter a whole number';
		return inExactRange(text, min, max) ? null : bounds(min, max);
	}
	if (typeof value === 'boolean') return 'Enter a number';
	const n = typeof value === 'number' ? value : FLOAT.test(text.trim()) ? Number(text) : NaN;
	if (!Number.isFinite(n)) return 'Enter a number';
	const low = c?.min ?? null;
	const high = c?.max ?? null;
	return (low !== null && n < low) || (high !== null && n > high) ? bounds(low, high) : null;
}
