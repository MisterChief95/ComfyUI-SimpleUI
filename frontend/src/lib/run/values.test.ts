import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dimensionPair } from '../controls/aspect.ts';
import type { ControlDescriptor } from '../contracts.ts';
import { baseValue, coerceValue, sameValue, validateValue } from './values.ts';
import { describeGenerationError, isTerminal, statusInfo } from './status.ts';
import { runShortcut } from './shortcuts.ts';

const make = (logical_type: ControlDescriptor['logical_type'], value: ControlDescriptor['value']) =>
	({ logical_type, value }) as ControlDescriptor;

test('aspect dimensions snap both bounds and steps and preserve exact integers', () => {
	const width = {
		...make('int', '1024'),
		component: 'number',
		constraints: { min: 64, max: 2048, exact_min: '64', exact_max: '2048', step: 64 }
	} as ControlDescriptor;
	const height = {
		...width,
		constraints: {
			...width.constraints!,
			min: 128,
			exact_min: '128',
			max: 1024,
			exact_max: '1024',
			step: 128
		}
	};
	assert.deepEqual(dimensionPair(width, height, 1152, 896), ['1152', '896']);
	assert.deepEqual(dimensionPair(width, height, 5000, 20), ['2048', '128']);
	assert.deepEqual(dimensionPair(width, height, 1200, 850), ['1216', '896']);
	const unlimited = { ...width, constraints: null };
	assert.deepEqual(dimensionPair(unlimited, unlimited, '9007199254740993', '1024'), [
		'9007199254740993',
		'1024'
	]);
	assert.equal(dimensionPair(width, height, NaN, 1024), null);
	assert.equal(dimensionPair({ ...width, component: 'readonly' }, height, 1024, 1024), null);
});

test('run shortcuts ignore editing, modifiers, composition, repeats and handled events', () => {
	const event = {
		key: 'p',
		ctrlKey: false,
		metaKey: false,
		altKey: false,
		shiftKey: false,
		repeat: false,
		isComposing: false,
		defaultPrevented: false
	};
	assert.equal(runShortcut(event, false), 'prompt');
	assert.equal(runShortcut({ ...event, key: 'r' }, false), 'seed');
	assert.equal(runShortcut(event, true), null);
	for (const flag of [
		'ctrlKey',
		'metaKey',
		'altKey',
		'shiftKey',
		'repeat',
		'isComposing',
		'defaultPrevented'
	]) {
		assert.equal(runShortcut({ ...event, [flag]: true }, false), null);
	}
	assert.equal(runShortcut({ ...event, key: 'Enter', ctrlKey: true }, true), 'generate');
	assert.equal(runShortcut({ ...event, key: 'Enter', metaKey: true }, true), 'generate');
	assert.equal(
		runShortcut({ ...event, key: 'Enter', ctrlKey: true, isComposing: true }, true),
		null
	);
});

test('ExactInt compares as strings, not numbers', () => {
	const seed = make('int', '12345678901234567890');
	assert.equal(sameValue(seed, '12345678901234567890', baseValue(seed)), true);
	assert.equal(sameValue(seed, '12345678901234567891', baseValue(seed)), false);
});

test('floats compare numerically; booleans accept the legacy strings', () => {
	assert.equal(sameValue(make('float', 0.5), '0.50', 0.5), true);
	assert.equal(sameValue(make('float', 0.5), '', 0.5), false);
	assert.equal(sameValue(make('boolean', false), 'false', false), true);
	assert.equal(sameValue(make('boolean', false), true, false), false);
});

test('null base values and coercion', () => {
	assert.equal(baseValue(make('boolean', null)), false);
	assert.equal(baseValue(make('string', null)), '');
	assert.equal(coerceValue(make('float', 0), '1.5'), 1.5);
	assert.equal(coerceValue(make('int', '0'), '7'), '7');
	assert.equal(coerceValue(make('boolean', false), 'true'), true);
});

test('status vocabulary and error summaries', () => {
	assert.equal(isTerminal('running'), false);
	assert.equal(isTerminal('submission_unknown'), false);
	assert.equal(isTerminal('succeeded'), true);
	assert.equal(
		statusInfo({ status: 'succeeded', output_state: 'partial' } as never).label,
		'Done (partial)'
	);
	const described = describeGenerationError({
		execution: { exception_message: 'Out of memory', node_type: 'KSampler', node_id: '5' }
	});
	assert.deepEqual(described?.summary, ['Out of memory (KSampler #5)']);
});

test('validateValue checks int syntax and exact bounds without rounding', () => {
	const seed = {
		logical_type: 'int',
		component: 'seed',
		constraints: {
			min: 0,
			max: 1.8446744073709552e19,
			step: null,
			exact_min: '0',
			exact_max: '18446744073709551615'
		}
	} as ControlDescriptor;
	assert.equal(validateValue(seed, '18446744073709551615'), null);
	assert.match(
		validateValue(seed, '18446744073709551616') ?? '',
		/between 0 and 18446744073709551615/
	);
	assert.match(validateValue(seed, '-1') ?? '', /between/);
	assert.equal(validateValue(seed, '1.5'), 'Enter a whole number');
	assert.equal(validateValue(seed, ''), 'Enter a whole number');
	assert.equal(validateValue(seed, '1e3'), 'Enter a whole number');
	assert.equal(validateValue(seed, '--1'), 'Enter a whole number');
	assert.equal(validateValue(seed, '01'), 'Enter a whole number');
	assert.ok(validateValue(seed, Number('9007199254740993')));
	assert.equal(validateValue(seed, '9007199254740993'), null);
	const slider = { ...seed, component: 'slider' } as ControlDescriptor;
	assert.equal(validateValue(slider, '1.5'), 'Enter a whole number');
	assert.equal(validateValue(slider, '9007199254740993'), null);
	const free = { logical_type: 'int', component: 'number', constraints: null } as ControlDescriptor;
	assert.equal(validateValue(free, '-7'), null);
	const ranged = {
		logical_type: 'int',
		component: 'number',
		constraints: { min: 1, max: 10, step: 1, exact_min: null, exact_max: null }
	} as ControlDescriptor;
	assert.equal(validateValue(ranged, '10'), null);
	assert.ok(validateValue(ranged, '11'));
	assert.ok(validateValue(ranged, '0'));
});

test('validateValue checks float finiteness and range; other types pass', () => {
	const f = {
		logical_type: 'float',
		component: 'number',
		constraints: { min: 0, max: 2, step: null, exact_min: null, exact_max: null }
	} as ControlDescriptor;
	assert.equal(validateValue(f, 1.5), null);
	assert.equal(validateValue(f, '0.25'), null);
	assert.equal(validateValue(f, '1e0'), null);
	assert.match(validateValue(f, 2.5) ?? '', /between 0 and 2/);
	assert.ok(validateValue(f, -0.1));
	assert.equal(validateValue(f, ''), 'Enter a number');
	assert.equal(validateValue(f, 'abc'), 'Enter a number');
	assert.equal(validateValue(f, '0x10'), 'Enter a number');
	assert.equal(validateValue(f, Infinity), 'Enter a number');
	assert.equal(validateValue(f, NaN), 'Enter a number');
	const open = {
		logical_type: 'float',
		component: 'number',
		constraints: null
	} as ControlDescriptor;
	assert.equal(validateValue(open, -1e9), null);
	assert.equal(validateValue(make('string', ''), ''), null);
	assert.equal(validateValue(make('boolean', false), true), null);
});

test('seed controls default to -1 (random) regardless of the imported seed', () => {
	const seed = { logical_type: 'int', component: 'seed', value: '42' } as ControlDescriptor;
	assert.equal(baseValue(seed), '-1');
	assert.equal(sameValue(seed, '42', baseValue(seed)), false);
});
