import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ControlDescriptor } from '../contracts.ts';
import { describeApply, planApply, presetValues } from './presets.ts';

const control = (
	id: string,
	logical_type: ControlDescriptor['logical_type'],
	value: ControlDescriptor['value'],
	component: ControlDescriptor['component'] = 'text'
) => ({ binding_id: id, logical_type, value, component }) as ControlDescriptor;

const controls = [
	control('1:seed', 'int', '5', 'seed'),
	control('2:cfg', 'float', 7),
	control('3:on', 'boolean', false, 'checkbox'),
	control('4:text', 'string', 'a'),
	control('5:img', 'string', 'x.png', 'file'),
	control('6:hid', 'string', 'h')
];

test('planApply coerces, drops unchanged, skips unknown/hidden/file', () => {
	const plan = planApply(controls, new Set(['6:hid']), {
		'1:seed': 123, // JSON number for an int -> ExactInt string
		'2:cfg': 7, // equals imported -> not a draft entry but counted as applied
		'3:on': true,
		'9:gone': 'x',
		'5:img': 'y.png',
		'6:hid': 'z'
	});
	assert.deepEqual(plan.draft, { '1:seed': '123', '3:on': true });
	assert.deepEqual([plan.applied, plan.unknown, plan.unusable], [3, 1, 2]);
	assert.equal(
		describeApply('P', plan),
		'Preset "P" applied: 3 values. 3 skipped (1 not in this workflow, 2 for hidden or file controls).'
	);
});

test('large ints stay exact strings', () => {
	const plan = planApply(controls, new Set(), { '1:seed': '18446744073709551615' });
	assert.equal(plan.draft['1:seed'], '18446744073709551615');
});

test('presetValues keeps existing non-file controls and finite numbers', () => {
	assert.deepEqual(
		presetValues(controls, {
			'1:seed': '9',
			'5:img': 'q',
			'9:gone': 'x',
			'2:cfg': Number.NaN,
			'4:text': 'b'
		}),
		{ '1:seed': '9', '4:text': 'b' }
	);
});
