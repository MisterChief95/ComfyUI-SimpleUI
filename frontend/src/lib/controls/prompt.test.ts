import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { insertText } from './prompt.ts';

test('prompt insertion preserves surrounding text and replaces the selection literally', () => {
	assert.equal(insertText('before AFTER', '<plain text>', 7, 12), 'before <plain text>');
	assert.equal(insertText('abcd', 'new', 2, 2), 'abnewcd');
	assert.equal(insertText('', 'prompt', 0, 0), 'prompt');
});

import { adjustWeight, completionAt, completionText, suggest } from './prompt.ts';

test('weight shortcut wraps, steps, and unwraps at 1.0', () => {
	let r = adjustWeight('a cat here', 4, 4, 0.1)!;
	assert.equal(r.value, 'a (cat:1.1) here');
	r = adjustWeight(r.value, r.start, r.end, 0.1)!;
	assert.equal(r.value, 'a (cat:1.2) here');
	r = adjustWeight(r.value, r.start, r.end, -0.1)!;
	r = adjustWeight(r.value, r.start, r.end, -0.1)!;
	assert.equal(r.value, 'a cat here');
	assert.equal(adjustWeight('a  b', 2, 2, 0.1), null);
});

test('weight shortcut handles selections, nested parens and caret inside the weight', () => {
	assert.equal(adjustWeight('red cat', 0, 7, 0.1)!.value, '(red cat:1.1)');
	assert.equal(adjustWeight('(a (b))', 0, 7, -0.1)!.value, '((a (b)):0.9)');
	assert.equal(adjustWeight('(cat:1.1)', 7, 7, 0.1)!.value, '(cat:1.2)');
	assert.equal(adjustWeight('(cat:1.0)', 0, 9, 0.1)!.value, '(cat:1.1)');
	assert.equal(adjustWeight('((x:1.1))', 2, 3, -0.1)!.value, '(x)');
});

test('completion tokens and suggestions', () => {
	assert.deepEqual(completionAt('a, embedding:ba', 15), {
		kind: 'embedding',
		start: 3,
		partial: 'ba'
	});
	assert.equal(completionAt('<lora:st', 8)?.kind, 'lora');
	assert.equal(completionAt('plain text', 10), null);
	assert.deepEqual(suggest(['my_bad', 'bad_hands', 'x'], 'bad'), ['bad_hands', 'my_bad']);
	assert.equal(completionText('lora', 'style/a.safetensors'), '<lora:style/a:1>');
	assert.equal(completionText('embedding', 'bad'), 'embedding:bad');
});
