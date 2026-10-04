import type { Style } from '$lib/contracts';

/** Selected style ids that still exist, in selection order. */
export function liveSelection(selected: string[], styles: Style[]): string[] {
	const ids = new Set(styles.map((style) => style.id));
	return selected.filter((id) => ids.has(id));
}

/** Toggle one id; selection order is application order. */
export function toggle(selected: string[], id: string): string[] {
	return selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id];
}
