import { test } from 'node:test';
import assert from 'node:assert/strict';
import { friendlySchema } from './labels.ts';
import type { ControlDescriptor, ControlSchema } from '../contracts.ts';

const control = (id: string, input_name: string, label: string): ControlDescriptor =>
	({ binding_id: id, input_name, label }) as ControlDescriptor;
const schema = (controls: ControlDescriptor[]): ControlSchema =>
	({ controls }) as unknown as ControlSchema;
const labels = (s: ControlSchema) => s.controls.map((c) => c.label);

test('generated labels become friendly when unique', () => {
	const result = friendlySchema(
		schema([
			control('1:cfg', 'cfg', 'KSampler: Cfg'),
			control('2:ckpt_name', 'ckpt_name', 'Load Checkpoint: Ckpt name'),
			control('3:batch_size', 'batch_size', 'Empty Latent Image: Batch size'),
			control('4:upscale_by', 'upscale_by', 'Upscale Image By: Upscale by')
		])
	);
	assert.deepEqual(labels(result), ['CFG scale', 'Checkpoint', 'Batch size', 'Upscale by']);
});

test('repeated stages keep their qualifier', () => {
	const input = schema([
		control('1:cfg', 'cfg', 'KSampler 3: Cfg'),
		control('2:cfg', 'cfg', 'KSampler 9: Cfg')
	]);
	assert.deepEqual(labels(friendlySchema(input)), ['KSampler 3: Cfg', 'KSampler 9: Cfg']);
});

test('a label that collides with an existing one is kept', () => {
	const input = schema([control('1:cfg', 'cfg', 'Sampler: Cfg'), control('2:x', 'x', 'CFG scale')]);
	assert.deepEqual(labels(friendlySchema(input)), ['Sampler: Cfg', 'CFG scale']);
});

test('saved relabels and unrelated labels are untouched', () => {
	const input = schema([
		control('1:cfg', 'cfg', 'Creativity'),
		control('2:text', 'text', 'Positive prompt'),
		control('3:cfg', 'cfg', 'Cfg')
	]);
	assert.deepEqual(labels(friendlySchema(input)), ['Creativity', 'Positive prompt', 'Cfg']);
});

test('returns the same schema object when nothing changes', () => {
	const input = schema([control('1:cfg', 'cfg', 'Creativity')]);
	assert.equal(friendlySchema(input), input);
});
