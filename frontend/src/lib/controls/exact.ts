// Exact-integer helpers for seed-like controls. ExactInt values are decimal
// strings; all arithmetic here goes through BigInt so nothing is rounded.
import { isExactInt } from '../contracts.ts';

/** Default seed range when a control declares none: an unsigned 64-bit value. */
export const DEFAULT_SEED_MAX = '18446744073709551615';

/** True if `value` is an ExactInt inside the optional [min, max] bounds (also ExactInt strings). */
export function inExactRange(value: string, min: string | null, max: string | null): boolean {
	if (!isExactInt(value)) return false;
	const n = BigInt(value);
	if (min !== null && isExactInt(min) && n < BigInt(min)) return false;
	if (max !== null && isExactInt(max) && n > BigInt(max)) return false;
	return true;
}

/** A uniformly random ExactInt in [min, max] (inclusive), using crypto randomness. */
export function randomExactInt(min: string, max: string): string {
	const lo = BigInt(min);
	const hi = BigInt(max);
	if (hi <= lo) return lo.toString();
	const span = hi - lo + 1n;
	// 8 spare bytes make the modulo bias negligible (< 2^-64).
	const bytes = new Uint8Array(Math.ceil(span.toString(16).length / 2) + 8);
	crypto.getRandomValues(bytes);
	let n = 0n;
	for (const byte of bytes) n = (n << 8n) | BigInt(byte);
	return (lo + (n % span)).toString();
}
