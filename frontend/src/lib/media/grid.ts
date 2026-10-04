export type GridRow = {
	start: number;
	end: number;
	top: number;
	height: number;
	group: string | null;
};

/** Square tile geometry, with full-width group rows. The item list stays intact. */
export function layoutGrid(
	count: number,
	width: number,
	minimum: number,
	gap: number,
	groupAt: (index: number) => string | null = () => null
): { rows: GridRow[]; columns: number; cell: number; height: number } {
	const columns = Math.max(1, Math.floor((width + gap) / (minimum + gap)));
	const cell = Math.max(1, (width - (columns - 1) * gap) / columns);
	const rows: GridRow[] = [];
	let top = 0;
	let start = 0;
	function add(end: number, group: string | null = null): void {
		const height = group ? 48 : cell;
		rows.push({ start, end, top, height, group });
		top += height + gap;
		start = end;
	}
	for (let index = 0; index < count; index++) {
		const group = groupAt(index);
		if (group) {
			if (index > start) add(index);
			add(index, group);
		}
		if (index - start + 1 === columns) add(index + 1);
	}
	if (start < count) add(count);
	return { rows, columns, cell, height: Math.max(0, top - gap) };
}

/** Binary search avoids scanning the library on every scroll event. */
export function visibleRows(
	rows: GridRow[],
	top: number,
	viewport: number,
	overscan = 600
): GridRow[] {
	const lower = top - overscan;
	const upper = top + viewport + overscan;
	let lo = 0;
	let hi = rows.length;
	while (lo < hi) {
		const mid = (lo + hi) >>> 1;
		if (rows[mid].top + rows[mid].height < lower) lo = mid + 1;
		else hi = mid;
	}
	const start = lo;
	while (lo < rows.length && rows[lo].top <= upper) lo++;
	return rows.slice(start, lo);
}
