import { test } from 'node:test';
import assert from 'node:assert/strict';
import { layoutChanges } from './conflict.ts';
import type { LayoutDoc } from '../contracts.ts';
import type { ControlDescriptor } from '../contracts.ts';
import {
	applyDraft,
	baselineDraft,
	buildPresentation,
	isRangedNumber,
	normalizePatch
} from './presentation.ts';

function control(over: Partial<ControlDescriptor>): ControlDescriptor {
	return {
		binding_id: '5:steps',
		node_id: '5',
		class_type: 'KSampler',
		input_name: 'steps',
		logical_type: 'int',
		value: '20',
		component: 'slider',
		group: 'generation',
		order: 0,
		label: 'Steps',
		help_text: null,
		constraints: { min: 1, max: 100, step: 1, exact_min: null, exact_max: null },
		options: null,
		multiline: false,
		inference_reason: 'x',
		unresolved: [],
		raw_metadata: null,
		...over
	};
}

test('conflict diff identifies section changes, item movement, span, hiding and removal', () => {
	const before: LayoutDoc = {
		version: 1,
		sections: [
			{
				id: 'a',
				title: 'Old',
				columns: 1,
				collapsed: false,
				items: [{ kind: 'control', binding_id: '5:steps', span: 'auto' }]
			}
		],
		hidden: ['6:cfg']
	};
	const after: LayoutDoc = {
		version: 1,
		sections: [
			{
				id: 'a',
				title: 'New',
				columns: 2,
				collapsed: true,
				items: [{ kind: 'control', binding_id: '6:cfg', span: 'full' }]
			}
		],
		hidden: ['5:steps']
	};
	assert.deepEqual(layoutChanges(before, before), []);
	const changes = layoutChanges(before, after);
	assert.equal(changes.length, 3);
	assert.match(changes[0], /Section Old.*New/);
	assert.ok(changes.some((change) => /5:steps.*hidden/.test(change)));
	assert.ok(changes.some((change) => /6:cfg.*hidden.*full/.test(change)));
	assert.match(
		layoutChanges(before, { version: 1, sections: [], hidden: [] })[0],
		/Section removed: Old/
	);
});

test('isRangedNumber excludes exact ints and non-numbers', () => {
	assert.equal(isRangedNumber(control({})), true);
	assert.equal(
		isRangedNumber(
			control({ constraints: { min: null, max: null, step: null, exact_min: '0', exact_max: '9' } })
		),
		false
	);
	assert.equal(isRangedNumber(control({ logical_type: 'string', constraints: null })), false);
});

test('normalizePatch drops unchanged fields', () => {
	const c = control({});
	assert.deepEqual(normalizePatch(c, { label: 'Steps ', display_max: '100' }), {});
	assert.deepEqual(normalizePatch(c, { label: 'Count', display_max: '50' }), {
		label: 'Count',
		display_max: '50'
	});
	assert.equal(baselineDraft(c).display_default, '20');
});

test('applyDraft changes label, range and default for a ranged int', () => {
	const next = applyDraft(control({}), {
		label: 'Count',
		display_max: '50',
		display_default: '30.7'
	});
	assert.equal(next.label, 'Count');
	assert.equal(next.constraints?.max, 50);
	assert.equal(next.constraints?.min, 1);
	assert.equal(next.value, '30');
});

test('buildPresentation keeps the saved fields and only writes changed ones', () => {
	const saved = { ...buildPresentation(null, { label: 'Old' }), group: 'model' as const };
	const out = buildPresentation(saved, { display_min: '', help_text: ' hi ', display_max: '40' });
	assert.equal(out.label, 'Old');
	assert.equal(out.group, 'model');
	assert.equal(out.help_text, 'hi');
	assert.equal(out.display_min, null);
	assert.equal(out.display_max, 40);
});

test('LoRA stack display options draft, preview and save as booleans', () => {
	const stack = control({ component: 'lora_stack', logical_type: 'string', constraints: null });
	assert.deepEqual(normalizePatch(stack, { show_clip: true }), {});
	assert.deepEqual(normalizePatch(stack, { show_clip: false }), { show_clip: false });
	const preview = applyDraft(stack, { show_thumbnails: false, show_clip: false });
	assert.equal(preview.show_thumbnails, false);
	assert.equal(preview.show_clip, false);
	const out = buildPresentation(null, { show_clip: false });
	assert.equal(out.show_clip, false);
	assert.equal(out.show_thumbnails, undefined);
});

test('LoRAs per row defaults to one and saves only when changed', () => {
	const stack = control({ component: 'lora_stack', logical_type: 'string', constraints: null });
	assert.equal(baselineDraft(stack).lora_columns, 1);
	assert.deepEqual(normalizePatch(stack, { lora_columns: 1 }), {});
	assert.deepEqual(normalizePatch(stack, { lora_columns: 3 }), { lora_columns: 3 });
	assert.equal(applyDraft(stack, { lora_columns: 2 }).lora_columns, 2);
	assert.equal(buildPresentation(null, { lora_columns: 2 }).lora_columns, 2);
	assert.equal(buildPresentation(null, {}).lora_columns, undefined);
});
