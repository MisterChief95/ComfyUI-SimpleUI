import { test } from 'node:test';
import assert from 'node:assert/strict';
import { matchOptions } from './match.ts';

test('matching ignores case and trims the search', () => {
	assert.deepEqual(matchOptions(['MODEL Alpha', 'other', 'model beta'], ' MoDeL '), [0, 2]);
});

test('substrings rank before subsequences, keeping original order within each rank', () => {
	assert.deepEqual(
		matchOptions(['aXbYc', 'second ABC', 'a_b_c', 'abc first', 'acb'], 'abc'),
		[1, 3, 0, 2]
	);
});

test('no match returns no indices, including repeated characters in the query', () => {
	assert.deepEqual(matchOptions(['abc', 'acb', ''], 'zz'), []);
	assert.deepEqual(matchOptions(['ab', 'a_b_a'], 'aa'), [1]);
});

test('empty search keeps every original index, including duplicate labels', () => {
	assert.deepEqual(matchOptions(['same', '', 'same'], ''), [0, 1, 2]);
});

test('subsequence matching keeps Unicode characters intact', () => {
	assert.deepEqual(matchOptions(['🌙 model alpha', '🌙a', 'alpha'], '🌙a'), [1, 0]);
});
