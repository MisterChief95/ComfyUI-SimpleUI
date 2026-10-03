import { test } from 'node:test';
import assert from 'node:assert/strict';
import { advance, isLeaf, sibling } from './walk.ts';

const folders = ['Date', 'Workflow', 'Favorites', 'Videos', 'Unsorted'].map((path) => ({ path, name: path, count: 1 }));

test('collection leaves walk in server order while the parent remains a branch', () => {
	assert.equal(isLeaf('Collections'), false);
	assert.equal(isLeaf('Collections/a'), true);
	const collections = ['a', 'b'].map((id) => ({ path: `Collections/${id}`, name: id, count: 1 }));
	assert.deepEqual(advance(collections, 'Collections/a', null), { group: 'Collections/b', position: null });
	assert.equal(advance(collections, 'Collections/b', null), null);
});

test('walk preserves server sibling order, skips branches, and stops at boundaries', () => {
	assert.equal(sibling(folders, 'Favorites', 1)?.path, 'Videos');
	assert.equal(sibling(folders, 'Videos', -1)?.path, 'Favorites');
	assert.equal(sibling(folders, 'Favorites', -1), null);
	assert.equal(sibling(folders, 'Unsorted', 1), null);
	assert.equal(sibling(folders, 'Workflow/missing', 1), null);
	assert.equal(isLeaf('Date/2026/10'), false);
	const days = ['03', '02', '01'].map((day) => ({ path: `Date/2026/10/${day}`, name: day, count: 1 }));
	assert.equal(sibling(days, days[0].path, 1)?.path, days[1].path);
	assert.equal(sibling(days, days[2].path, 1), null);
});

test('continuation keeps the position in its group and starts siblings without a reused cursor', () => {
	assert.deepEqual(advance(folders, 'Favorites', 'opaque-position'), { group: 'Favorites', position: 'opaque-position' });
	assert.deepEqual(advance(folders, 'Favorites', null), { group: 'Videos', position: null });
	assert.equal(advance(folders, 'Unsorted', null), null);
	assert.equal(advance([], 'Favorites', null), null);
	const workflows = ['Workflow/z', 'Workflow/a'].map((path) => ({ path, name: path, count: 1 }));
	assert.deepEqual(advance(workflows, 'Workflow/z', null), { group: 'Workflow/a', position: null });
});
