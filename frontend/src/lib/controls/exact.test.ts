import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_SEED_MAX, inExactRange, randomExactInt } from './exact.ts';

test('randomExactInt stays inside exact bounds beyond 2**53', () => {
	for (let i = 0; i < 200; i++) {
		const value = randomExactInt('0', DEFAULT_SEED_MAX);
		assert.ok(inExactRange(value, '0', DEFAULT_SEED_MAX), value);
	}
	assert.equal(randomExactInt('5', '5'), '5');
	assert.equal(randomExactInt('9007199254740993', '9007199254740993'), '9007199254740993');
	for (let i = 0; i < 50; i++) assert.ok(['-1', '0', '1'].includes(randomExactInt('-1', '1')));
});

test('inExactRange validates format and bounds exactly', () => {
	assert.equal(inExactRange('18446744073709551615', '0', DEFAULT_SEED_MAX), true);
	assert.equal(inExactRange('18446744073709551616', '0', DEFAULT_SEED_MAX), false);
	assert.equal(inExactRange('-1', '0', null), false);
	assert.equal(inExactRange('1.5', null, null), false);
	assert.equal(inExactRange('', null, null), false);
});
