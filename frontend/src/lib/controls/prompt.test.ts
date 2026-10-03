import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { insertText } from './prompt.ts';

test('prompt insertion preserves surrounding text and replaces the selection literally', () => {
	assert.equal(insertText('before AFTER', '<plain text>', 7, 12), 'before <plain text>');
	assert.equal(insertText('abcd', 'new', 2, 2), 'abnewcd');
	assert.equal(insertText('', 'prompt', 0, 0), 'prompt');
});
