import assert from 'node:assert/strict';
import { test } from 'node:test';
import { groupWarnings } from './warnings.ts';

const d = (code: string) => ({ field: null, code, message: code });

test('splits warnings into actionable, stale and informational', () => {
	const groups = groupWarnings([
		d('flexible_input'),
		d('undeclared_input'),
		d('inactive_controls'),
		d('mapping_correction_stale'),
		d('mapping_correction_stale'),
		d('something_else')
	]);
	assert.equal(groups.info.length, 3);
	assert.equal(groups.stale.length, 2);
	assert.deepEqual(
		groups.actionable.map((w) => w.code),
		['something_else']
	);
});
