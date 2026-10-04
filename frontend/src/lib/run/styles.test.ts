import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { liveSelection, toggle } from './styles.ts';

test('toggle keeps application order and liveSelection drops deleted styles', () => {
	assert.deepEqual(toggle(toggle([], 'a'), 'b'), ['a', 'b']);
	assert.deepEqual(toggle(['a', 'b'], 'a'), ['b']);
	const styles = [{ id: 'b' }, { id: 'c' }] as Parameters<typeof liveSelection>[1];
	assert.deepEqual(liveSelection(['a', 'b', 'c'], styles), ['b', 'c']);
});
