import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ControlDescriptor, LayoutDoc } from '../contracts.ts';
import { validate } from '../layout/model.ts';
import { copyKnown, describeCopy } from './copyLayout.ts';

const schema = {
	controls: ['1:a', '2:b', '3:c'].map((binding_id) => ({ binding_id }) as ControlDescriptor)
};
const source: LayoutDoc = {
	version: 1,
	sections: [
		{
			id: 's1',
			title: 'One',
			columns: 2,
			collapsed: false,
			items: [
				{ kind: 'control', binding_id: '1:a', span: 'full' },
				{ kind: 'control', binding_id: '9:x' }
			]
		},
		{
			id: 's2',
			title: 'Two',
			columns: 1,
			collapsed: true,
			items: [{ kind: 'control', binding_id: '8:y' }]
		}
	],
	hidden: ['3:c', '7:z']
};

test('copyKnown keeps only bindings present in the schema', () => {
	const { doc, kept, dropped } = copyKnown(source, schema);
	assert.equal(kept, 2);
	assert.equal(dropped, 3);
	assert.deepEqual(doc.sections[0].items, [{ kind: 'control', binding_id: '1:a', span: 'full' }]);
	assert.deepEqual(doc.sections[1].items, []);
	assert.deepEqual(doc.hidden, ['3:c']);
	assert.deepEqual(validate(doc), []);
	assert.equal(source.hidden.length, 2); // input untouched
	assert.match(describeCopy('Other', { doc, kept, dropped }), /2 controls; 3 dropped/);
});

test('copyKnown carries a whole integer pair or drops both bindings', () => {
	const paired: LayoutDoc = {
		version: 1,
		sections: [
			{
				...source.sections[0],
				items: [
					{
						kind: 'aspect_ratio',
						width: '1:a',
						height: '2:b',
						presets: [[1024, 1024]],
						span: 'full'
					}
				]
			}
		],
		hidden: []
	};
	const integers = {
		controls: schema.controls.map((c) => ({ ...c, logical_type: 'int' as const }))
	};
	assert.deepEqual(copyKnown(paired, integers), { doc: paired, kept: 2, dropped: 0 });
	for (const incompatible of [schema, { controls: integers.controls.slice(0, 1) }]) {
		const copied = copyKnown(paired, incompatible);
		assert.equal(copied.kept, 0);
		assert.equal(copied.dropped, 2);
		assert.deepEqual(copied.doc.sections[0].items, []);
	}
});

test('copyKnown filters panel columns and keeps the row/column structure', () => {
	const panels: LayoutDoc = {
		version: 2,
		sections: [
			{
				id: 'p',
				title: 'P',
				mode: 'panels',
				collapsed: false,
				rows: [
					{
						id: 'r1',
						columns: [
							{
								id: 'c1',
								items: [
									{ kind: 'control', binding_id: '1:a' },
									{ kind: 'control', binding_id: '9:x' }
								]
							},
							{ id: 'c2', items: [{ kind: 'control', binding_id: '2:b' }] }
						]
					}
				]
			}
		],
		hidden: []
	};
	const { doc, kept, dropped } = copyKnown(panels, schema);
	assert.equal(kept, 2);
	assert.equal(dropped, 1);
	const section = doc.sections[0];
	assert.ok(section.mode === 'panels');
	assert.deepEqual(
		section.rows[0].columns.map((c) => c.items.length),
		[1, 1]
	);
	assert.deepEqual(validate(doc), []);
});
