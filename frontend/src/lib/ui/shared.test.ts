import { test } from 'node:test';
import assert from 'node:assert/strict';
import { panelWidth, resizeKey } from './resize.ts';
import {
	readStored,
	writeStored,
	removeStored,
	readJson,
	writeJson,
	readFlag,
	writeFlag,
	readPanelWidth
} from './storage.ts';

test('device storage retains existing formats and fails safely when unavailable or corrupt', () => {
	const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
	const values = new Map<string, string>();
	try {
		Object.defineProperty(globalThis, 'localStorage', {
			configurable: true,
			value: {
				getItem: (key: string) => values.get(key) ?? null,
				setItem: (key: string, value: string) => values.set(key, value),
				removeItem: (key: string) => values.delete(key)
			}
		});
		assert.equal(readFlag('missing', true), true);
		writeFlag('flag', false);
		assert.equal(readStored('flag'), '0');
		assert.equal(readFlag('flag', true), false);
		writeFlag('flag', true);
		assert.equal(readStored('flag'), '1');
		const draft = { seed: '18446744073709551615', enabled: false, count: 3 };
		assert.equal(writeJson('draft', draft), true);
		assert.deepEqual(readJson('draft', {}), draft);
		writeStored('bad', '{');
		assert.deepEqual(readJson('bad', {}), {});
		assert.equal(writeJson('bigint', 1n), false);
		for (const invalid of ['', '0', '-1', 'Infinity', 'no width']) {
			writeStored('width', invalid);
			assert.equal(readPanelWidth('width'), null);
		}
		writeStored('width', '352');
		assert.equal(readPanelWidth('width'), 352);
		removeStored('draft');
		assert.equal(readStored('draft'), null);
		Object.defineProperty(globalThis, 'localStorage', {
			configurable: true,
			get() {
				throw new Error('blocked');
			}
		});
		assert.equal(readStored('draft'), null);
		assert.equal(writeStored('draft', 'value'), false);
		assert.equal(writeJson('draft', draft), false);
		assert.deepEqual(readJson('draft', {}), {});
		assert.equal(readFlag('flag', true), true);
		assert.doesNotThrow(() => removeStored('draft'));
	} finally {
		if (original) Object.defineProperty(globalThis, 'localStorage', original);
		else Reflect.deleteProperty(globalThis, 'localStorage');
	}
});

test('panel keyboard steps and bounds preserve run/viewer behavior', () => {
	assert.equal(resizeKey('ArrowLeft', 352, false, false), 368);
	assert.equal(resizeKey('ArrowRight', 352, true, false), 288);
	assert.equal(resizeKey('Home', 352, false, false), null);
	assert.equal(resizeKey('End', 352, false, false), null);
	assert.equal(resizeKey('Enter', 352, false, true), null);
	assert.equal(panelWidth(resizeKey('Home', 400, false, true)!, 1000, 260, 360), 640);
	assert.equal(panelWidth(resizeKey('End', 400, false, true)!, 1000, 260, 360), 260);
	assert.equal(panelWidth(500, 800, 240, 320), 480);
	assert.equal(panelWidth(200, 800, 240, 320), 240);
	assert.equal(panelWidth(300, 0, 240, 320), 240);
	assert.equal(panelWidth(352.6, 1000, 240, 320), 353);
});
