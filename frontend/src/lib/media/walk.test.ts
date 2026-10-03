import { test } from 'node:test';
import assert from 'node:assert/strict';
import { advance, isLeaf, sibling } from './walk.ts';

const folders = ['Workflow/a', 'Workflow/b', 'Workflow/c'].map((path) => ({
	path,
	name: path,
	count: 1
}));

test('collection leaves walk in server order while the parent remains a branch', () => {
	assert.equal(isLeaf('Collections'), false);
	assert.equal(isLeaf('Collections/a'), true);
	const collections = ['a', 'b'].map((id) => ({ path: `Collections/${id}`, name: id, count: 1 }));
	assert.deepEqual(advance(collections, 'Collections/a', null), {
		group: 'Collections/b',
		position: null
	});
	assert.equal(advance(collections, 'Collections/b', null), null);
});

test('top-level views never walk into each other', () => {
	const root = ['Date', 'Workflow', 'Favorites', 'Videos', 'Unsorted'].map((path) => ({
		path,
		name: path,
		count: 1
	}));
	for (const view of ['Favorites', 'Videos', 'Unsorted']) {
		assert.equal(isLeaf(view), false);
		assert.equal(sibling(root, view, 1), null);
		assert.equal(advance(root, view, null), null);
	}
});

test('walk preserves server sibling order, skips branches, and stops at boundaries', () => {
	assert.equal(sibling(folders, 'Workflow/a', 1)?.path, 'Workflow/b');
	assert.equal(sibling(folders, 'Workflow/b', -1)?.path, 'Workflow/a');
	assert.equal(sibling(folders, 'Workflow/a', -1), null);
	assert.equal(sibling(folders, 'Workflow/c', 1), null);
	assert.equal(sibling(folders, 'Workflow/missing', 1), null);
	assert.equal(isLeaf('Date/2026/10'), false);
	const days = ['03', '02', '01'].map((day) => ({
		path: `Date/2026/10/${day}`,
		name: day,
		count: 1
	}));
	assert.equal(sibling(days, days[0].path, 1)?.path, days[1].path);
	assert.equal(sibling(days, days[2].path, 1), null);
});

test('continuation keeps the position in its group and starts siblings without a reused cursor', () => {
	assert.deepEqual(advance(folders, 'Workflow/a', 'opaque-position'), {
		group: 'Workflow/a',
		position: 'opaque-position'
	});
	assert.deepEqual(advance(folders, 'Workflow/a', null), { group: 'Workflow/b', position: null });
	assert.equal(advance(folders, 'Workflow/c', null), null);
	assert.equal(advance([], 'Workflow/a', null), null);
	const workflows = ['Workflow/z', 'Workflow/a'].map((path) => ({ path, name: path, count: 1 }));
	assert.deepEqual(advance(workflows, 'Workflow/z', null), { group: 'Workflow/a', position: null });
});
