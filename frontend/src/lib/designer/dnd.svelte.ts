// One Pointer Events drag-and-drop implementation for mouse, pen and touch.
//
// A drag starts only from an element carrying `dnd.handle(...)` (the grip; it
// must have `touch-action: none`), after moving DRAG_THRESHOLD px. The handle
// captures the pointer, so every later move/up arrives there. Drop targets are
// found from the DOM by data attributes, so no zone registration is needed:
//
//   [data-section-list]   container of section cards (section reordering)
//   [data-section-card]   one section card (its [data-items] grid is the target)
//   [data-items=<id>]     the item grid of section <id>
//   [data-row/column]     optional panel row and column IDs on that grid
//   [data-item=<binding>] one rendered control inside a grid
//   [data-pair-height]   the second binding of a composite control
//   [data-autoscroll]     scrolling container the pointer may nudge near an edge
//
// The class only reports `target` (a section id + index among rendered,
// non-dragged items, or a section index); the editor maps that to a model edit.
// Escape, pointercancel or losing focus cancel the drag with no drop.
import type { Attachment } from 'svelte/attachments';
import type { PanelTarget } from '../layout/model';
import { insertionBar, insertionIndex, sectionIndex, type Bar, type Box } from './drop';

export type DragPayload = {
	/** item: a placed control; lib: a control from the library; section: a whole section. */
	type: 'item' | 'lib' | 'section';
	id: string;
	label: string;
};

export type DropTarget =
	| { type: 'items'; sectionId: string; index: number; panel?: PanelTarget }
	| { type: 'sections'; index: number };

export const DRAG_THRESHOLD = 6;
const EDGE = 64;
const MAX_SCROLL = 14;

function box(el: Element): Box {
	const r = el.getBoundingClientRect();
	return { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
}

export class Dnd {
	/** What is being dragged, once the threshold has been crossed. */
	payload = $state.raw<DragPayload | null>(null);
	pointer = $state.raw<{ x: number; y: number } | null>(null);
	target = $state.raw<DropTarget | null>(null);
	/** Insertion indicator in viewport pixels (fixed positioning). */
	bar = $state.raw<Bar | null>(null);
	/** Id of an empty section or panel column being hovered. */
	zone = $state<string | null>(null);

	private ondrop: (payload: DragPayload, target: DropTarget) => void;
	private scroller: HTMLElement | null = null;
	private velocity = 0;
	private frame = 0;
	private last = { x: 0, y: 0 };

	constructor(ondrop: (payload: DragPayload, target: DropTarget) => void) {
		this.ondrop = ondrop;
	}

	get active(): boolean {
		return this.payload !== null;
	}

	/** Attachment for a grip: `<span {@attach dnd.handle(() => ({ type, id, label }))}>`. */
	handle(get: () => DragPayload): Attachment<HTMLElement> {
		return (el) => {
			let pointerId: number | null = null;
			let startX = 0;
			let startY = 0;

			const finish = (drop: boolean): void => {
				if (pointerId === null) return;
				const id = pointerId;
				pointerId = null;
				window.removeEventListener('keydown', onKey, true);
				window.removeEventListener('blur', onAway);
				document.removeEventListener('visibilitychange', onHidden);
				if (el.hasPointerCapture?.(id)) el.releasePointerCapture(id);
				const payload = this.payload;
				const target = this.target;
				this.end();
				if (drop && payload && target) this.ondrop(payload, target);
			};

			const onKey = (event: KeyboardEvent): void => {
				if (event.key !== 'Escape') return;
				event.preventDefault();
				event.stopPropagation();
				finish(false);
			};
			// Alt-tab or a hidden tab mid-drag would otherwise leave the body overrides on.
			const onAway = (): void => finish(false);
			const onHidden = (): void => {
				if (document.hidden) finish(false);
			};
			const onDown = (event: PointerEvent): void => {
				if (event.button !== 0 || pointerId !== null || this.payload) return;
				pointerId = event.pointerId;
				startX = event.clientX;
				startY = event.clientY;
				window.addEventListener('keydown', onKey, true);
				window.addEventListener('blur', onAway);
				document.addEventListener('visibilitychange', onHidden);
				try {
					el.setPointerCapture(event.pointerId);
				} catch {
					// A pointer that already ended; the drag simply never starts.
				}
			};
			const onMove = (event: PointerEvent): void => {
				if (event.pointerId !== pointerId) return;
				if (!this.payload) {
					if (Math.hypot(event.clientX - startX, event.clientY - startY) < DRAG_THRESHOLD) return;
					this.begin(get());
				}
				this.update(event.clientX, event.clientY);
			};
			const onUp = (event: PointerEvent): void => {
				if (event.pointerId === pointerId) finish(true);
			};
			const onCancel = (event: PointerEvent): void => {
				if (event.pointerId === pointerId) finish(false);
			};

			el.addEventListener('pointerdown', onDown);
			el.addEventListener('pointermove', onMove);
			el.addEventListener('pointerup', onUp);
			el.addEventListener('pointercancel', onCancel);
			el.addEventListener('lostpointercapture', onCancel);
			return () => {
				finish(false);
				el.removeEventListener('pointerdown', onDown);
				el.removeEventListener('pointermove', onMove);
				el.removeEventListener('pointerup', onUp);
				el.removeEventListener('pointercancel', onCancel);
				el.removeEventListener('lostpointercapture', onCancel);
			};
		};
	}

	private begin(payload: DragPayload): void {
		this.payload = payload;
		document.body.style.userSelect = 'none';
		document.body.style.cursor = 'grabbing';
	}

	private end(): void {
		cancelAnimationFrame(this.frame);
		this.frame = 0;
		this.velocity = 0;
		this.scroller = null;
		this.payload = null;
		this.pointer = null;
		this.target = null;
		this.bar = null;
		this.zone = null;
		document.body.style.userSelect = '';
		document.body.style.cursor = '';
	}

	private update(x: number, y: number): void {
		this.last = { x, y };
		this.pointer = { x, y };
		this.scrollNear(x, y);
		this.hit(x, y);
	}

	/** Nudge the scrolling container under the pointer when it is near an edge. */
	private scrollNear(x: number, y: number): void {
		const scroller =
			document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-autoscroll]') ?? null;
		this.scroller = scroller;
		this.velocity = 0;
		if (scroller) {
			const r = scroller.getBoundingClientRect();
			if (y < r.top + EDGE) this.velocity = -MAX_SCROLL * Math.min(1, (r.top + EDGE - y) / EDGE);
			else if (y > r.bottom - EDGE)
				this.velocity = MAX_SCROLL * Math.min(1, (y - (r.bottom - EDGE)) / EDGE);
		}
		if (this.velocity !== 0 && !this.frame) this.frame = requestAnimationFrame(this.tick);
	}

	private tick = (): void => {
		this.frame = 0;
		if (!this.payload || !this.scroller || this.velocity === 0) return;
		this.scroller.scrollTop += this.velocity;
		// Layout moved under a stationary pointer, so recompute the target.
		this.hit(this.last.x, this.last.y);
		this.frame = requestAnimationFrame(this.tick);
	};

	private hit(x: number, y: number): void {
		const payload = this.payload;
		if (!payload) return;
		const under = document.elementFromPoint(x, y);
		this.target = null;
		this.bar = null;
		this.zone = null;

		if (payload.type === 'section') {
			const list =
				under?.closest('[data-section-list]') ??
				under?.closest('[data-autoscroll]')?.querySelector('[data-section-list]') ??
				null;
			if (!list) return;
			const cards = [...list.querySelectorAll(':scope > [data-section-card]')].filter(
				(el) => (el as HTMLElement).dataset.sectionCard !== payload.id
			);
			const boxes = cards.map(box);
			const index = sectionIndex(boxes, y);
			this.target = { type: 'sections', index };
			this.bar = insertionBar(boxes, index, list.getBoundingClientRect().width);
			return;
		}

		const grid =
			under?.closest<HTMLElement>('[data-items]') ??
			under?.closest('[data-section-card]')?.querySelector<HTMLElement>('[data-items]') ??
			null;
		if (!grid) return;
		const sectionId = grid.dataset.items ?? '';
		const items = [...grid.querySelectorAll(':scope > [data-item]')].filter(
			(el) =>
				(el as HTMLElement).dataset.item !== payload.id &&
				(el as HTMLElement).dataset.pairHeight !== payload.id
		);
		const boxes = items.map(box);
		const width = grid.getBoundingClientRect().width;
		const index = insertionIndex(boxes, x, y, width);
		const panel =
			grid.dataset.row && grid.dataset.column
				? { row: grid.dataset.row, column: grid.dataset.column }
				: undefined;
		this.target = { type: 'items', sectionId, index, panel };
		this.bar = insertionBar(boxes, index, width);
		if (boxes.length === 0) this.zone = panel?.column ?? sectionId;
	}
}
