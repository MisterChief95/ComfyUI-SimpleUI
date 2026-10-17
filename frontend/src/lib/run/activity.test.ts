import { test } from 'node:test';
import assert from 'node:assert/strict';
import { badge, summarize } from './activity.ts';

test('summarize splits running from waiting', () => {
	const result = summarize([{ status: 'running' }, { status: 'queued' }, { status: 'submitting' }]);
	assert.deepEqual(result, { running: 1, waiting: 2 });
});

test('badge is hidden when idle and describes counts otherwise', () => {
	assert.equal(badge({ running: 0, waiting: 0 }), null);
	assert.deepEqual(badge({ running: 1, waiting: 2 }), { text: '3', label: '1 running, 2 waiting' });
	assert.deepEqual(badge({ running: 0, waiting: 1 }), { text: '1', label: '1 waiting' });
	assert.equal(badge({ running: 100, waiting: 0 })?.text, '99+');
});
