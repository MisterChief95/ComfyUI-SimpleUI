import { on } from 'svelte/events';

/** Keep overlay input local while preserving native controls and dialog dismissal. */
export function isolateInput(element: HTMLElement): () => void {
	const events = [
		'click',
		'dblclick',
		'contextmenu',
		'pointerdown',
		'pointerup',
		'pointermove',
		'pointercancel',
		'mousedown',
		'mouseup',
		'mousemove',
		'touchstart',
		'touchmove',
		'touchend',
		'touchcancel',
		'wheel',
		'keydown',
		'keyup'
	];
	const stop = (event: Event): void => event.stopPropagation();
	const cleanup = events.map((event) => on(element, event, stop));
	return () => {
		for (const detach of cleanup) detach();
	};
}

/** Native auto-popovers light-dismiss before click, exposing the underlying target. */
export function isolatePopoverInput(element: HTMLElement): () => void {
	const cleanup = isolateInput(element);
	let dismissing = false;
	const outside = (event: Event): boolean =>
		element.matches(':popover-open') &&
		!event
			.composedPath()
			.some(
				(target) =>
					target === element || (target as HTMLButtonElement).popoverTargetElement === element
			);
	const down = (event: Event): void => {
		dismissing = outside(event);
		if (dismissing) {
			event.preventDefault();
			event.stopImmediatePropagation();
		}
	};
	const up = (event: Event): void => {
		if (dismissing) {
			event.preventDefault();
			event.stopImmediatePropagation();
		}
	};
	const click = (event: Event): void => {
		if (!dismissing && !outside(event)) return;
		dismissing = false;
		event.preventDefault();
		event.stopImmediatePropagation();
		if (element.matches(':popover-open')) element.hidePopover();
	};
	const cancel = (): void => {
		dismissing = false;
	};
	document.addEventListener('pointerdown', down, true);
	document.addEventListener('pointerup', up, true);
	document.addEventListener('click', click, true);
	document.addEventListener('pointercancel', cancel, true);
	return () => {
		cleanup();
		document.removeEventListener('pointerdown', down, true);
		document.removeEventListener('pointerup', up, true);
		document.removeEventListener('click', click, true);
		document.removeEventListener('pointercancel', cancel, true);
	};
}
