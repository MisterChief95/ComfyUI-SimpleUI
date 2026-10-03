// Pure rect math for drag and drop in the designer. No Svelte, no DOM: node
// runs it (`npm test`), so keep to erasable TypeScript.
import type { LayoutItem } from '../contracts.ts';
import { itemBindings } from '../layout/model.ts';

/** Translate a rendered drop position to the final index in one leaf list.
 * Stale leaves stay in place; the dragged leaf (including both dimensions of a
 * composite) is removed before counting. Rendered IDs may name a surviving half.
 */
export function dropIndex(items: LayoutItem[], renderedIds: string[], binding: string, domIndex: number): number {
	const remaining = items.filter((item) => !itemBindings(item).includes(binding));
	const visible = renderedIds.filter((id) => remaining.some((item) => itemBindings(item).includes(id)));
	if (domIndex >= visible.length) return remaining.length;
	return remaining.findIndex((item) => itemBindings(item).includes(visible[domIndex]));
}

export interface Box {
	left: number;
	top: number;
	right: number;
	bottom: number;
}

export interface Bar {
	left: number;
	top: number;
	width: number;
	height: number;
}

const FULL_RATIO = 0.75;
const BAR = 3;
const GAP = 5;

/** A box that spans (nearly) the whole row, so it is ordered by y rather than x. */
function isFull(box: Box, rowWidth: number): boolean {
	return box.right - box.left >= rowWidth * FULL_RATIO;
}

function distance(box: Box, x: number, y: number): number {
	const dx = Math.max(box.left - x, 0, x - box.right);
	const dy = Math.max(box.top - y, 0, y - box.bottom);
	return Math.hypot(dx, dy);
}

/**
 * Where a dragged item would be inserted among `boxes` (grid children in
 * order, the dragged one excluded). Returns the final index (0..boxes.length).
 * The nearest box wins; the point then falls before or after it.
 */
export function insertionIndex(boxes: Box[], x: number, y: number, rowWidth: number): number {
	if (boxes.length === 0) return 0;
	let best = 0;
	let bestDistance = Infinity;
	boxes.forEach((box, i) => {
		const d = distance(box, x, y);
		if (d < bestDistance) {
			best = i;
			bestDistance = d;
		}
	});
	const b = boxes[best];
	const centerX = (b.left + b.right) / 2;
	const centerY = (b.top + b.bottom) / 2;
	let after: boolean;
	if (isFull(b, rowWidth)) after = y > centerY;
	else if (y >= b.top && y <= b.bottom) after = x > centerX;
	else after = y > centerY;
	return best + (after ? 1 : 0);
}

/** Final position of a dragged section among the other sections' boxes (by y). */
export function sectionIndex(boxes: Box[], y: number): number {
	let index = 0;
	for (const box of boxes) if ((box.top + box.bottom) / 2 < y) index += 1;
	return index;
}

/**
 * The insertion indicator for `index` among `boxes`, or null when there are no
 * boxes (the caller highlights the empty zone instead). A full-row neighbour
 * gets a horizontal bar; a one-cell neighbour gets a vertical one.
 */
export function insertionBar(boxes: Box[], index: number, rowWidth: number): Bar | null {
	if (boxes.length === 0) return null;
	const before = index < boxes.length;
	const ref = before ? boxes[index] : boxes[boxes.length - 1];
	const width = ref.right - ref.left;
	const height = ref.bottom - ref.top;
	if (isFull(ref, rowWidth)) {
		return {
			left: ref.left,
			top: before ? ref.top - GAP : ref.bottom + GAP - BAR,
			width,
			height: BAR
		};
	}
	return {
		left: before ? ref.left - GAP : ref.right + GAP - BAR,
		top: ref.top,
		width: BAR,
		height
	};
}
