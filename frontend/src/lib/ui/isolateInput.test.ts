import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isolateInput, isolatePopoverInput } from './isolateInput.ts';

test('overlay input stops propagation, preserves defaults and detaches cleanly', async () => {
	const element = new EventTarget();
	const cleanup = isolateInput(element as HTMLElement);
	await Promise.resolve();
	for (const type of [
		'click',
		'pointerdown',
		'pointerup',
		'touchmove',
		'wheel',
		'keydown',
		'keyup'
	]) {
		const check = (event: Event): void => {
			assert.equal(event.cancelBubble, true, type);
			assert.equal(event.defaultPrevented, false, type);
		};
		element.addEventListener(type, check);
		element.dispatchEvent(new Event(type, { bubbles: true, cancelable: true }));
		element.removeEventListener(type, check);
	}
	cleanup();
	element.addEventListener('keydown', (event) => assert.equal(event.cancelBubble, false));
	element.dispatchEvent(new Event('keydown', { bubbles: true }));
});

test('popover dismissal consumes the outside gesture even after native light-dismiss', async () => {
	const previous = Object.getOwnPropertyDescriptor(globalThis, 'document');
	const document = new EventTarget();
	Object.defineProperty(globalThis, 'document', { value: document, configurable: true });
	let open = true;
	const popup = Object.assign(new EventTarget(), {
		matches: () => open,
		hidePopover: () => {
			open = false;
		}
	});
	const cleanup = isolatePopoverInput(popup as unknown as HTMLElement);
	await Promise.resolve();
	let underlying = 0;
	document.addEventListener('click', () => underlying++);
	try {
		for (const type of ['pointerdown', 'pointerup', 'click']) {
			if (type === 'click') open = false;
			const event = new Event(type, { bubbles: true, cancelable: true });
			document.dispatchEvent(event);
			assert.equal(event.defaultPrevented, true, type);
		}
		assert.equal(underlying, 0);
		cleanup();
		document.dispatchEvent(new Event('click'));
		assert.equal(underlying, 1);
	} finally {
		cleanup();
		if (previous) Object.defineProperty(globalThis, 'document', previous);
		else Reflect.deleteProperty(globalThis, 'document');
	}
});

test('popover triggers preserve clicks and long-press release while the popup is open', async () => {
	const previous = Object.getOwnPropertyDescriptor(globalThis, 'document');
	const document = new EventTarget();
	Object.defineProperty(globalThis, 'document', { value: document, configurable: true });
	let open = true;
	const popup = Object.assign(new EventTarget(), {
		matches: () => open,
		hidePopover: () => {
			open = false;
		}
	});
	const trigger = Object.assign(new EventTarget(), { popoverTargetElement: popup });
	const icon = new EventTarget();
	const cleanup = isolatePopoverInput(popup as unknown as HTMLElement);
	await Promise.resolve();
	let clicks = 0;
	document.addEventListener('click', () => clicks++);
	try {
		for (const longPress of [false, true]) {
			open = !longPress;
			for (const type of ['pointerdown', 'pointerup', 'click']) {
				// A long press shows the popup after pointerdown, before its release/click.
				if (longPress && type === 'pointerup') open = true;
				const event = new Event(type, { bubbles: true, cancelable: true });
				Object.defineProperty(event, 'composedPath', { value: () => [icon, trigger, document] });
				document.dispatchEvent(event);
				assert.equal(event.defaultPrevented, false, `${longPress}: ${type}`);
			}
			assert.equal(open, true);
		}
		assert.equal(clicks, 2);
	} finally {
		cleanup();
		if (previous) Object.defineProperty(globalThis, 'document', previous);
		else Reflect.deleteProperty(globalThis, 'document');
	}
});
