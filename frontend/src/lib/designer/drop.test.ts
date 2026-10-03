import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dropIndex, insertionBar, insertionIndex, sectionIndex, type Box } from './drop.ts';
import type { LayoutDoc, LayoutItem } from '../contracts.ts';
import { locate, placeControl } from '../layout/model.ts';

const control = (binding_id: string): LayoutItem => ({ kind: 'control', binding_id });
const pair: LayoutItem = { kind: 'aspect_ratio', width: 'width', height: 'height', span: 'full', presets: [[512, 512]] };

test('dropIndex skips stale leaves and removes the dragged item before counting', () => {
	const items = [control('stale'), control('a'), control('b'), control('stale2'), control('c')];
	assert.equal(dropIndex(items, ['a', 'b', 'c'], 'a', 0), 1);
	assert.equal(dropIndex(items, ['a', 'b', 'c'], 'a', 1), 3);
	assert.equal(dropIndex(items, ['a', 'b', 'c'], 'c', 0), 1);
	assert.equal(dropIndex(items, ['a', 'b', 'c'], 'c', 2), 4);
	assert.equal(dropIndex(items, ['a', 'b', 'c'], 'library', 1), 2);
	assert.equal(dropIndex([], [], 'library', 0), 0);
	assert.equal(dropIndex([control('stale')], [], 'library', 0), 1);
});

test('dropIndex treats composites and a rendered surviving half as one leaf', () => {
	const items = [control('stale'), pair, control('b')];
	assert.equal(dropIndex(items, ['width', 'b'], 'height', 0), 1);
	assert.equal(dropIndex(items, ['width', 'b'], 'width', 1), 2);
	assert.equal(dropIndex(items, ['height', 'b'], 'library', 0), 1);
	assert.equal(dropIndex(items, ['height', 'b'], 'height', 0), 1);
});

test('column-local drops reorder and move across rows, preserving stale leaves and composites', () => {
	const doc: LayoutDoc = { version: 2, hidden: [], sections: [{
		id: 'panels', title: 'Panels', mode: 'panels', collapsed: false,
		rows: [
			{ id: 'r1', columns: [
				{ id: 'c1', items: [control('a'), pair, control('stale')] },
				{ id: 'c2', items: [control('stale2'), control('b')] }
			] },
			{ id: 'r2', columns: [{ id: 'empty', items: [] }] }
		]
	}] };
	const section = doc.sections[0];
	assert.ok(section.mode === 'panels');
	const source = section.rows[0].columns[0].items;
	const reordered = placeControl(doc, 'height', 'panels', dropIndex(source, ['a', 'width'], 'height', 0), { row: 'r1', column: 'c1' });
	assert.deepEqual(locate(reordered, 'height'), { section: 'panels', row: 'r1', column: 'c1', index: 0 });
	const dest = section.rows[0].columns[1].items;
	const moved = placeControl(doc, 'width', 'panels', dropIndex(dest, ['b'], 'width', 0), { row: 'r1', column: 'c2' });
	assert.ok(moved.sections[0].mode === 'panels');
	assert.deepEqual(moved.sections[0].rows[0].columns[1].items, [control('stale2'), pair, control('b')]);
	assert.deepEqual(moved.sections[0].rows[0].columns[0].items, [control('a'), control('stale')]);
	const empty = placeControl(moved, 'height', 'panels', dropIndex([], [], 'height', 0), { row: 'r2', column: 'empty' });
	assert.deepEqual(locate(empty, 'width'), { section: 'panels', row: 'r2', column: 'empty', index: 0 });
	assert.deepEqual(source, [control('a'), pair, control('stale')]);
});

test('library and unplaced bindings drop into the requested column', () => {
	const doc: LayoutDoc = { version: 2, hidden: ['library'], sections: [{
		id: 'p', title: 'Panels', mode: 'panels', collapsed: false,
		rows: [{ id: 'r', columns: [{ id: 'first', items: [] }, { id: 'target', items: [control('stale')] }] }]
	}] };
	const target = { row: 'r', column: 'target' };
	const added = placeControl(doc, 'library', 'p', dropIndex([control('stale')], [], 'library', 0), target);
	const next = placeControl(added, 'unplaced', 'p', dropIndex([control('stale'), control('library')], ['library'], 'unplaced', 0), target);
	assert.deepEqual(locate(next, 'unplaced'), { section: 'p', ...target, index: 1 });
	assert.deepEqual(locate(next, 'library'), { section: 'p', ...target, index: 2 });
	assert.deepEqual(next.hidden, []);
});

const box = (left: number, top: number, right: number, bottom: number): Box => ({
	left,
	top,
	right,
	bottom
});

// Two columns, two rows, 200px wide row.
const grid = [box(0, 0, 96, 40), box(104, 0, 200, 40), box(0, 48, 96, 88), box(104, 48, 200, 88)];
// One full-width column.
const column = [box(0, 0, 200, 40), box(0, 48, 200, 88), box(0, 96, 200, 136)];

test('insertionIndex: empty list', () => {
	assert.equal(insertionIndex([], 10, 10, 200), 0);
});

test('insertionIndex: two-column grid orders by x within a row', () => {
	assert.equal(insertionIndex(grid, 10, 20, 200), 0);
	assert.equal(insertionIndex(grid, 90, 20, 200), 1);
	assert.equal(insertionIndex(grid, 150, 20, 200), 1);
	assert.equal(insertionIndex(grid, 190, 20, 200), 2);
	assert.equal(insertionIndex(grid, 20, 70, 200), 2);
	assert.equal(insertionIndex(grid, 190, 90, 200), 4);
});

test('insertionIndex: full-width items order by y', () => {
	assert.equal(insertionIndex(column, 100, 5, 200), 0);
	assert.equal(insertionIndex(column, 100, 30, 200), 1);
	assert.equal(insertionIndex(column, 100, 60, 200), 1);
	assert.equal(insertionIndex(column, 100, 200, 200), 3);
});

test('sectionIndex counts boxes above the pointer', () => {
	const sections = [box(0, 0, 200, 100), box(0, 110, 200, 300)];
	assert.equal(sectionIndex(sections, 10), 0);
	assert.equal(sectionIndex(sections, 120), 1);
	assert.equal(sectionIndex(sections, 400), 2);
	assert.equal(sectionIndex([], 5), 0);
});

test('insertionBar: horizontal for full rows, vertical for cells, none when empty', () => {
	assert.equal(insertionBar([], 0, 200), null);
	const h = insertionBar(column, 1, 200);
	assert.ok(h && h.height < h.width && h.width === 200 && h.top < 48);
	const end = insertionBar(column, 3, 200);
	assert.ok(end && end.top > 136);
	const v = insertionBar(grid, 1, 200);
	assert.ok(v && v.width < v.height && v.left < 104);
	const last = insertionBar(grid, 4, 200);
	assert.ok(last && last.left > 200 - 5);
});
