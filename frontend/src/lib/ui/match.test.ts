import { test } from 'node:test';
import assert from 'node:assert/strict';
import { matchOptions } from './match.ts';

test('matching ignores case, trims, and ranks substrings before subsequences in original order', () => {
	assert.deepEqual(matchOptions(['MODEL Alpha', 'other', 'model beta'], ' MoDeL '), [0, 2]);
	assert.deepEqual(
		matchOptions(['aXbYc', 'second ABC', 'a_b_c', 'abc first', 'acb'], 'abc'),
		[1, 3, 0, 2]
	);
	assert.deepEqual(matchOptions(['🌙 model alpha', '🌙a', 'alpha'], '🌙a'), [1, 0]);
});

test('no match returns nothing; empty search keeps every index', () => {
	assert.deepEqual(matchOptions(['abc', 'acb', ''], 'zz'), []);
	assert.deepEqual(matchOptions(['ab', 'a_b_a'], 'aa'), [1]);
	assert.deepEqual(matchOptions(['same', '', 'same'], ''), [0, 1, 2]);
});
