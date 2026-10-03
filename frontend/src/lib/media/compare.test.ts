import { test } from 'node:test';
import assert from 'node:assert/strict';
import { canReuseBoth, diffValues } from './compare.ts';

test('diffValues puts differences first and keeps exact seed strings', () => {
	const rows = diffValues({ '3:seed': '9007199254740993', '3:steps': 20, '5:text': 'cat' }, { '3:seed': '9007199254740995', '3:steps': 20 });
	assert.deepEqual(rows.map((r) => [r.key, r.same]), [['3:seed', false], ['5:text', false], ['3:steps', true]]);
	assert.equal(rows[1].b, '—');
	assert.equal(diffValues(null, null).length, 0);
});

test('canReuseBoth needs the same workflow and saved values on both', () => {
	const g = (workflow_id: string | null, effective_values: unknown = { x: 1 }) => ({ workflow_id, effective_values });
	assert.equal(canReuseBoth(g('w'), g('w')), true);
	assert.equal(canReuseBoth(g('w'), g('v')), false);
	assert.equal(canReuseBoth(g('w'), g('w', null)), false);
	assert.equal(canReuseBoth(g(null), g(null)), false);
	assert.equal(canReuseBoth(null, g('w')), false);
});
