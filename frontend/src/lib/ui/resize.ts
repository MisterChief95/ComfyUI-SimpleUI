/** Right-hand panel bounds, leaving room for the main content. */
export function panelWidth(px: number, total: number, min: number, remaining: number): number {
	return Math.round(Math.min(Math.max(px, min), Math.max(min, total - remaining)));
}

export function resizeKey(
	key: string,
	width: number,
	shift: boolean,
	extremes: boolean
): number | null {
	const step = shift ? 64 : 16;
	if (key === 'ArrowLeft') return width + step;
	if (key === 'ArrowRight') return width - step;
	if (extremes && key === 'Home') return Infinity;
	if (extremes && key === 'End') return 0;
	return null;
}
