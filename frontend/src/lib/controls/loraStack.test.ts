import { test } from 'node:test';
import assert from 'node:assert/strict';
import { newEntry, parseStack, serializeStack, stackProblem } from './loraStack.ts';

const CATALOG = ['a.safetensors', 'styles\\b.safetensors'];

test('unknown fields survive a parse, edit and serialize round trip', () => {
	const text = JSON.stringify({
		schema: 1,
		future: { x: 1 },
		loras: [
			{ name: 'a.safetensors', strength_model: 1, strength_clip: 0.5, enabled: true, note: 'kept' }
		]
	});
	const parsed = parseStack(text);
	assert.ok(parsed.ok);
	parsed.doc.loras[0].enabled = false;
	parsed.doc.loras.push(newEntry('styles\\b.safetensors', 'b style'));
	const out = JSON.parse(serializeStack(parsed.doc));
	assert.deepEqual(out.future, { x: 1 });
	assert.equal(out.loras[0].note, 'kept');
	assert.equal(out.loras[0].trigger_words, '');
	assert.equal(out.loras[1].name, 'styles/b.safetensors');
});

test('unreadable payloads are reported, not repaired', () => {
	for (const text of [
		'{bad',
		'[]',
		'{"schema":2,"loras":[]}',
		'{"schema":1}',
		'{"schema":1,"loras":[{"name":"a","strength_model":"1","strength_clip":1,"enabled":true}]}',
		'{"schema":1,"loras":[{"name":"a","strength_model":1,"strength_clip":1,"enabled":1}]}'
	])
		assert.equal(parseStack(text).ok, false, text);
});

test('only enabled LoRAs missing from the catalog block submission', () => {
	const stack = (enabled: boolean): string =>
		JSON.stringify({
			schema: 1,
			loras: [
				{ name: 'styles/b.safetensors', strength_model: 1, strength_clip: 1, enabled: true },
				{ name: 'gone.safetensors', strength_model: 1, strength_clip: 1, enabled }
			]
		});
	assert.equal(stackProblem(stack(false), CATALOG), null);
	assert.match(stackProblem(stack(true), CATALOG) ?? '', /gone\.safetensors/);
});
