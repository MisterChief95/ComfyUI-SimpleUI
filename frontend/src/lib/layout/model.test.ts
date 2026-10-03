import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ControlDescriptor, Group, LayoutDoc } from '../contracts.ts';
import {
	addSection,
	canAddSection,
	canPlace,
	itemCount,
	itemId,
	pairDimensions,
	splitDimensions,
	MAX_ITEMS,
	MAX_SECTIONS,
	defaultLayout,
	hide,
	locate,
	moveItem,
	moveSection,
	newSectionId,
	placeControl,
	removeSection,
	removeStale,
	renameSection,
	resolveLayout,
	setSpan,
	setToggle,
	setWhen,
	unhide,
	unplace,
	updateSection,
	validate,
	sectionItems,
	mapSectionItems,
	normalizeLayout,
	MAX_ROWS,
	MAX_COLUMNS,
	setSectionMode,
	addRow,
	removeRow,
	shiftRow,
	setRowColumns,
	removeColumn
} from './model.ts';

function control(id: string, group: Group, order: number, component = 'text'): ControlDescriptor {
	return {
		binding_id: id,
		node_id: id.split(':')[0],
		class_type: 'Node',
		input_name: id.split(':')[1],
		logical_type: 'string',
		value: '',
		component,
		group,
		order,
		label: id,
		help_text: null,
		constraints: null,
		options: null,
		multiline: false,
		inference_reason: '',
		unresolved: [],
		raw_metadata: null
	} as ControlDescriptor;
}

const schema = {
	controls: [
		control('3:b', 'advanced', 3),
		control('1:a', 'prompts', 0, 'textarea'),
		control('2:c', 'generation', 2),
		control('2:d', 'generation', 1),
		control('9:z', 'inactive', 4)
	]
};

const doc = (): LayoutDoc => ({
	version: 1,
	sections: [
		{
			id: 'one',
			title: 'One',
			columns: 1,
			collapsed: false,
			items: [
				{ kind: 'control', binding_id: 'a', span: 'full' },
				{ kind: 'control', binding_id: 'b', span: 'auto' }
			]
		},
		{ id: 'two', title: 'Two', columns: 2, collapsed: false, items: [] }
	],
	hidden: ['h']
});

const ids = (d: LayoutDoc, section: string): string[] =>
	d.sections.find((s) => s.id === section)!.items.map(itemId);

test('aspect ratio: placement, paired uniqueness, movement, hiding and splitting', () => {
	const paired = pairDimensions(doc(), 'a', 'b');
	assert.deepEqual(paired.sections[0].items, [
		{ kind: 'aspect_ratio', width: 'a', height: 'b', span: 'full' }
	]);
	assert.equal(itemCount(paired), 3);
	assert.deepEqual(locate(paired, 'b'), { section: 'one', index: 0 });
	assert.deepEqual(validate(paired), []);
	assert.ok(validate({ ...paired, hidden: ['b'] }).length);
	const moved = moveItem(paired, 'b', 'two');
	assert.equal(moved.sections[0].items.length, 0);
	assert.deepEqual(ids(moved, 'two'), ['a']);
	assert.deepEqual(hide(moved, 'b').hidden, ['h', 'a', 'b']);
	assert.deepEqual(ids(splitDimensions(moved, 'b'), 'two'), ['a', 'b']);
	const dimensions = {
		controls: ['a', 'b'].map((id) => ({
			...control(id, 'dimensions', 0, 'number'),
			logical_type: 'int' as const
		}))
	};
	const resolved = resolveLayout(dimensions, paired);
	assert.equal(resolved.sections[0].controls[0].height?.binding_id, 'b');
	assert.equal(resolved.unplaced.length, 0);
	assert.deepEqual(resolveLayout({ controls: dimensions.controls.slice(0, 1) }, paired).stale, [
		'b',
		'h'
	]);
	assert.equal(
		resolveLayout({ controls: dimensions.controls.slice(0, 1) }, paired).sections[0].controls[0]
			.control.binding_id,
		'a'
	);
	const before = doc();
	before.sections[0].items = [
		{ kind: 'control', binding_id: 'b' },
		{ kind: 'control', binding_id: 'a' },
		{ kind: 'control', binding_id: 'c' }
	];
	assert.deepEqual(ids(pairDimensions(before, 'a', 'b'), 'one'), ['a', 'c']);
});

test('defaultLayout: one titled section per non-empty group, ordered, collapsed rules', () => {
	const layout = defaultLayout(schema);
	assert.deepEqual(
		layout.sections.map((s) => [s.id, s.title, s.collapsed]),
		[
			['prompts', 'Prompts', false],
			['generation', 'Generation', false],
			['advanced', 'Advanced', true],
			['inactive', 'Inactive', true]
		]
	);
	assert.deepEqual(ids(layout, 'generation'), ['2:d', '2:c']);
	assert.equal(layout.sections[0].items[0].span, 'full');
	assert.deepEqual(validate(layout), []);
	assert.deepEqual(layout.hidden, []);
});

test('resolveLayout: null layout uses the automatic one; nothing unplaced', () => {
	const r = resolveLayout(schema, null);
	assert.equal(r.unplaced.length, 0);
	assert.equal(r.sections.length, 4);
	assert.equal(r.stale.length, 0);
});

test('resolveLayout: unplaced, stale, hidden', () => {
	const layout: LayoutDoc = {
		version: 1,
		sections: [
			{
				id: 's',
				title: 'S',
				columns: 1,
				collapsed: false,
				items: [
					{ kind: 'control', binding_id: '1:a' },
					{ kind: 'control', binding_id: 'gone:x' }
				]
			}
		],
		hidden: ['3:b', 'gone:y']
	};
	const r = resolveLayout(schema, layout);
	assert.deepEqual(
		r.sections[0].controls.map((c) => c.control.binding_id),
		['1:a']
	);
	assert.deepEqual(r.stale, ['gone:x', 'gone:y']);
	assert.deepEqual(
		r.hidden.map((c) => c.binding_id),
		['3:b']
	);
	assert.deepEqual(
		r.unplaced.map((c) => c.binding_id),
		['2:d', '2:c', '9:z']
	);
	// the input doc keeps stale entries
	assert.equal(layout.sections[0].items.length, 2);
});

test('addSection / removeSection / rename / update / move are immutable', () => {
	const d = doc();
	const frozen = JSON.stringify(d);
	const added = addSection(d, '  New  ', 1, 'n1');
	assert.deepEqual(
		added.sections.map((s) => s.id),
		['one', 'n1', 'two']
	);
	assert.equal(added.sections[1].title, 'New');
	assert.equal(addSection(d, '   ').sections[2].title, 'Section');
	assert.equal(addSection(d, 'x'.repeat(200)).sections[2].title.length, 80);
	assert.deepEqual(validate(added), []);

	const removed = removeSection(d, 'one');
	assert.deepEqual(
		removed.sections.map((s) => s.id),
		['two']
	);
	assert.deepEqual(removed.hidden, ['h']);
	assert.equal(
		resolveLayout({ controls: [control('a', 'prompts', 0)] }, removed).unplaced.length,
		1
	);
	assert.equal(removeSection(d, 'nope'), d);

	assert.equal(renameSection(d, 'two', ' Deux ').sections[1].title, 'Deux');
	assert.equal(renameSection(d, 'two', '  ').sections[1].title, 'Two');
	const upd = updateSection(d, 'two', { columns: 3, collapsed: true });
	assert.deepEqual([upd.sections[1].columns, upd.sections[1].collapsed], [3, true]);

	assert.deepEqual(
		moveSection(d, 0, 1).sections.map((s) => s.id),
		['two', 'one']
	);
	assert.equal(moveSection(d, 0, 0), d);
	assert.deepEqual(
		moveSection(d, 0, 99).sections.map((s) => s.id),
		['two', 'one']
	);
	assert.equal(JSON.stringify(d), frozen);
});

test('placeControl removes it from everywhere (sections and hidden), keeps span', () => {
	const d = doc();
	const fromHidden = placeControl(d, 'h', 'two');
	assert.deepEqual(fromHidden.hidden, []);
	assert.deepEqual(ids(fromHidden, 'two'), ['h']);
	assert.equal(fromHidden.sections[1].items[0].span, 'auto');

	const moved = placeControl(d, 'a', 'two', 0);
	assert.deepEqual(ids(moved, 'one'), ['b']);
	assert.deepEqual(ids(moved, 'two'), ['a']);
	assert.equal(moved.sections[1].items[0].span, 'full');

	assert.deepEqual(ids(placeControl(d, 'new', 'one', 1), 'one'), ['a', 'new', 'b']);
	assert.equal(placeControl(d, 'a', 'nope'), d);
	assert.deepEqual(validate(moved), []);
});

test('moveItem reorders within and across sections; ignores unplaced/hidden', () => {
	const d = doc();
	assert.deepEqual(ids(moveItem(d, 'a', 'one', 1), 'one'), ['b', 'a']);
	assert.deepEqual(ids(moveItem(d, 'b', 'two', 0), 'two'), ['b']);
	assert.equal(moveItem(d, 'h', 'one', 0), d);
	assert.equal(moveItem(d, 'missing', 'one', 0), d);
});

test('unplace / hide / unhide / removeStale', () => {
	const d = doc();
	const u = unplace(d, 'a');
	assert.equal(locate(u, 'a'), null);
	assert.equal(unplace(u, 'a'), u);

	const h = hide(d, 'a');
	assert.equal(locate(h, 'a'), 'hidden');
	assert.deepEqual(h.hidden, ['h', 'a']);
	assert.equal(hide(h, 'a'), h);
	assert.deepEqual(hide(d, 'fresh').hidden, ['h', 'fresh']);

	const un = unhide(d, 'h');
	assert.equal(locate(un, 'h'), null);
	assert.equal(unhide(un, 'h'), un);

	assert.equal(locate(removeStale(d, 'h'), 'h'), null);
	assert.deepEqual(locate(d, 'b'), { section: 'one', index: 1 });
});

test('setSpan only touches placed items', () => {
	const d = doc();
	assert.equal(setSpan(d, 'b', 'full').sections[0].items[1].span, 'full');
	assert.equal(setSpan(d, 'h', 'full'), d);
	assert.equal(setSpan(d, 'zz', 'full'), d);
});

test('newSectionId is valid and unique', () => {
	const taken = new Set<string>();
	for (let i = 0; i < 200; i++) {
		const id = newSectionId(taken);
		assert.match(id, /^[A-Za-z0-9_-]{1,40}$/);
		assert.ok(!taken.has(id));
		taken.add(id);
	}
});

test('validate enforces every invariant', () => {
	const ok = doc();
	assert.deepEqual(validate(ok), []);
	// eslint-disable-next-line @typescript-eslint/no-explicit-any -- tests deliberately corrupt the doc
	const bad = (mutate: (d: any) => void): string[] => {
		const d = structuredClone(ok);
		mutate(d);
		return validate(d);
	};
	assert.ok(validate(null).length);
	assert.ok(bad((d) => (d.version = 3)).some((e) => /version/.test(e)));
	assert.ok(bad((d) => (d.sections[0].id = 'bad id!')).some((e) => /id must match/.test(e)));
	assert.ok(bad((d) => (d.sections[1].id = 'one')).some((e) => /duplicate section/.test(e)));
	assert.ok(bad((d) => (d.sections[0].title = '   ')).some((e) => /title/.test(e)));
	assert.ok(bad((d) => (d.sections[0].title = 'x'.repeat(81))).some((e) => /title/.test(e)));
	assert.ok(bad((d) => (d.sections[0].columns = 4)).some((e) => /columns/.test(e)));
	assert.ok(bad((d) => (d.sections[0].collapsed = 'no')).some((e) => /collapsed/.test(e)));
	assert.ok(bad((d) => (d.sections[0].items[0].kind = 'aspect')).some((e) => /kind/.test(e)));
	assert.ok(bad((d) => (d.sections[0].items[0].span = 'wide')).some((e) => /span/.test(e)));
	assert.ok(bad((d) => (d.sections[0].items[0].binding_id = '')).some((e) => /binding_id/.test(e)));
	assert.ok(
		bad((d) => (d.sections[0].items[0].binding_id = 'x'.repeat(201))).some((e) =>
			/binding_id/.test(e)
		)
	);
	assert.ok(bad((d) => d.hidden.push('a')).some((e) => /more than once/.test(e)));
	assert.ok(bad((d) => d.sections[1].items.push({ kind: 'control', binding_id: 'b' })).length);
	assert.ok(
		bad((d) => {
			for (let i = 0; i < 41; i++) d.sections.push({ ...d.sections[1], id: `x${i}` });
		}).some((e) => /sections/.test(e))
	);
	assert.ok(
		bad((d) => {
			for (let i = 0; i < 1000; i++) d.hidden.push(`b${i}`);
		}).some((e) => /at most 1000/.test(e))
	);
});

test('operations are no-ops at the section and item limits', () => {
	const d = doc();
	assert.equal(canAddSection(d), true);
	const full: LayoutDoc = {
		...d,
		sections: Array.from({ length: MAX_SECTIONS }, (_, i) => ({
			id: `x${i}`,
			title: 'X',
			columns: 1 as const,
			collapsed: false,
			items: []
		}))
	};
	assert.equal(canAddSection(full), false);
	assert.equal(addSection(full, 'More'), full);
	assert.deepEqual(validate(full), []);

	// Fill the item cap: one section holding MAX_ITEMS - 1 controls plus one hidden.
	const crowded: LayoutDoc = {
		version: 1,
		sections: [
			{
				id: 'big',
				title: 'Big',
				columns: 1,
				collapsed: false,
				items: Array.from({ length: MAX_ITEMS - 1 }, (_, i) => ({
					kind: 'control' as const,
					binding_id: `b${i}`,
					span: 'auto' as const
				}))
			}
		],
		hidden: ['h']
	};
	assert.equal(itemCount(crowded), MAX_ITEMS);
	assert.equal(canPlace(crowded, 'new'), false);
	assert.equal(placeControl(crowded, 'new', 'big'), crowded);
	assert.equal(hide(crowded, 'new'), crowded);
	// Already-present bindings can still move or be hidden: the count does not grow.
	assert.equal(canPlace(crowded, 'b0'), true);
	assert.equal(canPlace(crowded, 'h'), true);
	assert.deepEqual(validate(hide(crowded, 'b0')), []);
	assert.equal(itemCount(hide(crowded, 'b0')), MAX_ITEMS);
	assert.equal(locate(placeControl(crowded, 'h', 'big'), 'h') !== 'hidden', true);
});

test('conditions and header switches resolve, move and validate', () => {
	const on = { ...control('9:on', 'advanced', 0), logical_type: 'boolean' as const };
	const schema = { controls: [control('1:a', 'prompts', 0), control('1:b', 'prompts', 1), on] };
	let doc: LayoutDoc = {
		version: 1,
		sections: [
			{
				id: 's1',
				title: 'S',
				columns: 1,
				collapsed: false,
				items: [
					{ kind: 'control', binding_id: '1:a' },
					{ kind: 'control', binding_id: '1:b' },
					{ kind: 'control', binding_id: '9:on' }
				]
			}
		],
		hidden: []
	};
	doc = setWhen(doc, '1:a', '9:on');
	assert.equal(setWhen(doc, '1:a', '1:a'), doc, 'an item cannot gate itself');
	assert.equal(resolveLayout(schema, doc).sections[0].controls[0].when?.binding_id, '9:on');
	assert.equal(
		resolveLayout(schema, setWhen(doc, '1:b', '1:a')).sections[0].controls[1].when,
		undefined,
		'non-boolean condition is ignored'
	);

	// The switch moves out of the items into the header and counts once.
	const switched = setToggle(doc, 's1', '9:on');
	assert.deepEqual(switched.sections[0].items.map(itemId), ['1:a', '1:b']);
	assert.equal(switched.sections[0].toggle, '9:on');
	assert.deepEqual(locate(switched, '9:on'), { section: 's1', index: -1 });
	assert.equal(itemCount(switched), 3);
	const resolved = resolveLayout(schema, switched);
	assert.equal(resolved.sections[0].toggle?.binding_id, '9:on');
	assert.deepEqual(resolved.unplaced, []);
	assert.deepEqual(validate(switched), []);

	// Hiding or placing the switch control takes it out of the header.
	assert.equal(hide(switched, '9:on').sections[0].toggle, null);
	assert.equal(placeControl(switched, '9:on', 's1').sections[0].toggle, null);
	assert.equal(setToggle(switched, 's1', null).sections[0].toggle, null);

	assert.notDeepEqual(validate({ ...switched, hidden: ['9:on'] }), [], 'switch is a placement');
	assert.notDeepEqual(validate(setWhen(doc, '1:a', '')), []);
});

function panels(): LayoutDoc {
	return {
		version: 2,
		sections: [
			{
				id: 'p',
				title: 'Panels',
				mode: 'panels',
				collapsed: false,
				rows: [
					{
						id: 'r1',
						columns: [
							{ id: 'c1', items: [{ kind: 'control', binding_id: 'a', span: 'full' }] },
							{ id: 'c2', items: [{ kind: 'control', binding_id: 'b' }] }
						]
					},
					{ id: 'r2', columns: [{ id: 'c3', items: [{ kind: 'control', binding_id: 'c' }] }] }
				]
			},
			{ id: 'auto', title: 'Auto', mode: 'auto', collapsed: true, columns: 2, items: [] }
		],
		hidden: ['h']
	};
}

test('v1 normalization preserves all bindings, order and presentation without mutation', () => {
	const legacy = doc();
	const snapshot = JSON.stringify(legacy);
	const normalized = normalizeLayout(legacy);
	assert.equal(normalized.version, 2);
	assert.deepEqual(
		normalized.sections.map((s) => s.mode),
		['auto', 'auto']
	);
	assert.deepEqual(normalized.sections.map(sectionItems), legacy.sections.map(sectionItems));
	assert.deepEqual(normalized.hidden, legacy.hidden);
	assert.deepEqual(validate(normalized), []);
	assert.equal(JSON.stringify(legacy), snapshot);
});

test('addSection rejects duplicate/invalid explicit ids and honors the final available slot', () => {
	const d = doc();
	assert.equal(addSection(d, 'Duplicate', undefined, 'one'), d);
	assert.equal(addSection(d, 'Invalid', undefined, 'bad id'), d);
	const almostFull = {
		...d,
		sections: Array.from({ length: MAX_SECTIONS - 1 }, (_, i) => ({
			id: `s${i}`,
			title: 'S',
			columns: 1 as const,
			collapsed: false,
			items: []
		}))
	};
	const full = addSection(almostFull, 'Last', undefined, 'last');
	assert.equal(full.sections.length, MAX_SECTIONS);
	assert.deepEqual(validate(full), []);
	assert.equal(addSection(full, 'Too many'), full);
});

test('panel leaves: location, pairing across columns, moving, editing and removal preserve uniqueness', () => {
	const d = panels();
	const snapshot = JSON.stringify(d);
	const target = { row: 'r2', column: 'c3' };
	assert.deepEqual(sectionItems(d.sections[0]).map(itemId), ['a', 'b', 'c']);
	assert.deepEqual(locate(d, 'b'), { section: 'p', row: 'r1', column: 'c2', index: 0 });
	const pair = pairDimensions(d, 'b', 'a');
	assert.deepEqual(locate(pair, 'a'), { section: 'p', row: 'r1', column: 'c2', index: 0 });
	assert.equal(itemCount(pair), 4);
	assert.deepEqual(sectionItems(splitDimensions(pair, 'a').sections[0]).map(itemId), [
		'b',
		'a',
		'c'
	]);
	const moved = moveItem(pair, 'a', 'p', 0, target);
	assert.deepEqual(locate(moved, 'b'), { section: 'p', ...target, index: 0 });
	assert.deepEqual(hide(moved, 'a').hidden, ['h', 'b', 'a']);
	assert.equal(locate(unplace(moved, 'a'), 'b'), null);
	assert.equal(sectionItems(setSpan(d, 'b', 'full').sections[0])[1].span, 'full');
	assert.equal(sectionItems(setWhen(d, 'b', 'on').sections[0])[1].when, 'on');
	assert.equal(locate(setToggle(d, 'auto', 'b'), 'b') !== null, true);
	assert.equal(placeControl(d, 'a', 'p', 0, { row: 'bad', column: 'c3' }), d);
	assert.equal(placeControl(d, 'a', 'auto', 0, target), d);
	assert.deepEqual(locate(placeControl(d, 'h', 'p'), 'h'), {
		section: 'p',
		row: 'r1',
		column: 'c1',
		index: 1
	});
	const empty: LayoutDoc = {
		...d,
		sections: [{ id: 'p', title: 'P', mode: 'panels', collapsed: false, rows: [] }]
	};
	assert.equal(
		placeControl(empty, 'h', 'p'),
		empty,
		'empty panel has no destination; UI must create a row first'
	);
	for (const result of [
		pair,
		moved,
		hide(moved, 'a'),
		splitDimensions(pair, 'a'),
		setSpan(d, 'b', 'full'),
		setWhen(d, 'b', 'on'),
		setToggle(d, 'auto', 'b'),
		placeControl(d, 'a', 'auto'),
		removeStale(d, 'a'),
		removeSection(d, 'p'),
		updateSection(d, 'p', { title: 'New', columns: 3, collapsed: true })
	]) {
		assert.deepEqual(validate(result), []);
	}
	const all = { controls: ['a', 'b', 'c', 'h'].map((id) => control(id, 'advanced', 0)) };
	assert.deepEqual(resolveLayout(all, pair).unplaced, []);
	assert.deepEqual(resolveLayout({ controls: all.controls.slice(1) }, pair).stale, ['a']);
	assert.equal(
		resolveLayout({ controls: all.controls.slice(1) }, pair).sections[0].controls[0].control
			.binding_id,
		'b'
	);
	assert.equal(JSON.stringify(d), snapshot);
});

test('panel validation caps structure and bindings, including pairs and header toggles', () => {
	const d = panels();
	// eslint-disable-next-line @typescript-eslint/no-explicit-any -- tests deliberately corrupt the doc
	const bad = (mutate: (d: any) => void): void => {
		const copy = structuredClone(d);
		mutate(copy);
		assert.ok(validate(copy).length);
	};
	bad((d) => (d.sections[0].rows[0].columns[1].id = 'c1'));
	bad((d) => (d.sections[0].rows[1].id = 'r1'));
	bad((d) => (d.sections[0].rows[1].columns[0].id = 'r1'));
	bad((d) => d.sections.push({ ...d.sections[0], id: 'p2' }));
	bad((d) => (d.sections[0].rows[0].columns = []));
	bad(
		(d) =>
			(d.sections[0].rows[0].columns = Array.from({ length: MAX_COLUMNS + 1 }, (_, i) => ({
				id: `c${i}`,
				items: []
			})))
	);
	bad(
		(d) =>
			(d.sections[0].rows = Array.from({ length: MAX_ROWS + 1 }, (_, i) => ({
				id: `r${i}`,
				columns: [{ id: `c${i}`, items: [] }]
			})))
	);
	bad((d) => (d.sections[0].items = []));
	bad((d) => (d.sections[0].rows[0].columns[0].rows = []));
	bad((d) => (d.sections[0].rows[0].columns[0].items = [{ kind: 'row', columns: [] }]));
	bad((d) => (d.sections[0].toggle = 'c'));
	bad((d) => d.hidden.push('b'));
	const crowded = {
		...pairDimensions(d, 'a', 'b'),
		hidden: Array.from({ length: MAX_ITEMS - 3 }, (_, i) => `h${i}`)
	};
	assert.equal(itemCount(crowded), MAX_ITEMS);
	assert.deepEqual(validate(crowded), []);
	assert.equal(placeControl(crowded, 'new', 'p'), crowded);
	assert.equal(setToggle(crowded, 'auto', 'new'), crowded);
	assert.deepEqual(validate(hide(crowded, 'a')), []);
	assert.deepEqual(validate(moveItem(crowded, 'a', 'p', 0, { row: 'r2', column: 'c3' })), []);
	assert.ok(validate({ ...crowded, hidden: [...crowded.hidden, 'new'] }).length);
	assert.ok(
		validate({
			...crowded,
			sections: crowded.sections.map((s) => (s.id === 'auto' ? { ...s, toggle: 'new' } : s))
		}).length
	);
	assert.deepEqual(
		sectionItems(
			mapSectionItems(d.sections[0], (items) => items.filter((i) => itemId(i) !== 'b'))
		).map(itemId),
		['a', 'c']
	);
});

test('panel structure: mode round trip, rows, columns and removal keep bindings valid', () => {
	const auto: LayoutDoc = {
		version: 2,
		sections: [
			{
				id: 's',
				title: 'S',
				mode: 'auto',
				columns: 2,
				collapsed: false,
				items: ['a', 'b', 'c'].map((binding_id) => ({ kind: 'control' as const, binding_id }))
			}
		],
		hidden: []
	};
	const snapshot = JSON.stringify(auto);
	const p = setSectionMode(auto, 's', 'panels');
	const sec = p.sections[0];
	assert.equal(sec.mode, 'panels');
	assert.ok(sec.mode === 'panels');
	assert.deepEqual(
		sec.rows[0].columns.map((c) => c.items.map(itemId)),
		[['a', 'c'], ['b']]
	);
	assert.deepEqual(validate(p), []);
	const back = setSectionMode(p, 's', 'auto');
	assert.ok(back.sections[0].mode !== 'panels');
	assert.equal(back.sections[0].columns, 2);
	assert.deepEqual(sectionItems(back.sections[0]).map(itemId), ['a', 'c', 'b']);
	assert.deepEqual(validate(back), []);

	const rows = addRow(addRow(p, 's', 3), 's', 1, 0);
	const r = rows.sections[0];
	assert.ok(r.mode === 'panels');
	assert.deepEqual(
		r.rows.map((row) => row.columns.length),
		[1, 2, 3]
	);
	const ids = r.rows.flatMap((row) => [row.id, ...row.columns.map((c) => c.id)]);
	assert.equal(new Set(ids).size, ids.length, 'structural ids unique');
	assert.deepEqual(validate(rows), []);
	const first = r.rows[1].id;
	const up = shiftRow(rows, 's', first, -1).sections[0];
	assert.ok(up.mode === 'panels' && up.rows[0].id === first);
	assert.equal(shiftRow(rows, 's', r.rows[0].id, -1), rows);

	const shrunk = setRowColumns(rows, 's', first, 1);
	assert.deepEqual(
		sectionItems(shrunk.sections[0]).map(itemId),
		['a', 'c', 'b'],
		'shrinking merges, never drops'
	);
	const grown = setRowColumns(rows, 's', r.rows[0].id, 3);
	assert.deepEqual(validate(grown), []);
	assert.equal(itemCount(grown), 3);

	const noCol = removeColumn(rows, 's', first, r.rows[1].columns[1].id);
	assert.equal(locate(noCol, 'b'), null, 'removed column unplaces its controls');
	assert.equal(locate(removeRow(rows, 's', first), 'a'), null);
	const unknown = removeColumn(rows, 's', 'missing', 'x');
	assert.deepEqual(unknown, rows);
	assert.equal(setSectionMode(auto, 's', 'auto'), auto);
	assert.equal(addRow(auto, 's'), auto, 'rows only apply to panel sections');
	assert.equal(JSON.stringify(auto), snapshot);
});
