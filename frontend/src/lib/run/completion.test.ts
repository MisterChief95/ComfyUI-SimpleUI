import { test } from 'node:test';
import assert from 'node:assert/strict';
import { justSucceeded, pageTitle } from './completion.ts';

test('title shows percentage while running and plain name otherwise', () => {
	assert.equal(pageTitle('Flux', 'running', { value: 5, max: 20 }), '25% · Flux · SimpleUI');
	assert.equal(pageTitle('Flux', 'running', null), 'Running · Flux · SimpleUI');
	assert.equal(pageTitle('Flux', 'queued', null), 'Queued · Flux · SimpleUI');
	assert.equal(pageTitle(null, 'succeeded', null), 'Generate · SimpleUI');
	assert.equal(pageTitle('Flux', 'running', { value: 30, max: 20 }), '100% · Flux · SimpleUI');
});

test('sound only fires on a live transition to success', () => {
	const running = { id: 'a', status: 'running' };
	assert.equal(justSucceeded(running, { id: 'a', status: 'succeeded' }), true);
	assert.equal(justSucceeded(null, { id: 'a', status: 'succeeded' }), false);
	assert.equal(
		justSucceeded({ id: 'a', status: 'succeeded' }, { id: 'a', status: 'succeeded' }),
		false
	);
	assert.equal(justSucceeded(running, { id: 'a', status: 'failed' }), false);
	assert.equal(justSucceeded(running, { id: 'b', status: 'succeeded' }), false);
});
