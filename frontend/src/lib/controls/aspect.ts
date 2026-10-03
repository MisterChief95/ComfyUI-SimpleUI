import type { ControlDescriptor } from '../contracts.ts';

export const DIMENSION_PRESETS: [number, number][] = [[1024, 1024], [1152, 896], [1216, 832], [1344, 768], [1536, 640]];

/** Snap positive dimensions to each binding's declared integer lattice. */
export function dimensionValue(control: ControlDescriptor, requested: number | string): string | null {
	if (control.logical_type !== 'int' || control.component === 'readonly' || (typeof requested === 'number' ? !Number.isSafeInteger(requested) : !/^-?(0|[1-9][0-9]*)$/.test(requested))) return null;
	const c = control.constraints;
	const min = c?.exact_min != null ? BigInt(c.exact_min) : BigInt(Math.ceil(c?.min ?? 1));
	const max = c?.exact_max != null ? BigInt(c.exact_max) : c?.max != null ? BigInt(Math.floor(c.max)) : null;
	const step = BigInt(Math.max(1, Math.ceil(c?.step ?? 1)));
	const base = min;
	const low = min > 0n ? min : base + ((1n - base + step - 1n) / step) * step;
	const high = max == null ? null : base + ((max - base) / step) * step;
	if (high != null && high < low) return null;
	let value = BigInt(requested);
	if (value < low) value = low;
	value = base + ((value - base + step / 2n) / step) * step;
	if (high != null && value > high) value = high;
	return value.toString();
}

export function dimensionPair(width: ControlDescriptor, height: ControlDescriptor, w: number | string, h: number | string): [string, string] | null {
	const left = dimensionValue(width, w), right = dimensionValue(height, h);
	return left != null && right != null ? [left, right] : null;
}
