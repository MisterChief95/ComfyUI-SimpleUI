import assert from 'node:assert/strict';
import { test } from 'node:test';
import { nodeProgress, parseFrame, previewUrl } from './events.ts';

test('parseFrame accepts frames and rejects junk', () => {
	assert.deepEqual(parseFrame('{"generation_id":"g","type":"progress","data":{"value":1}}'), {
		generation_id: 'g',
		type: 'progress',
		data: { value: 1 }
	});
	assert.deepEqual(parseFrame('{"generation_id":"g","type":"reconciled"}')?.data, {});
	for (const bad of ['nope', '[]', '{"type":"x"}', '{"generation_id":1,"type":"x"}', null]) {
		assert.equal(parseFrame(bad), null);
	}
});

test('previewUrl builds a data URL only for jpeg/png base64', () => {
	assert.equal(previewUrl({ mime: 'image/jpeg', image: 'QUJD' }), 'data:image/jpeg;base64,QUJD');
	assert.equal(previewUrl({ mime: 'image/png', image: 'QUI=' }), 'data:image/png;base64,QUI=');
	assert.equal(previewUrl({ mime: 'text/html', image: 'QUJD' }), null);
	assert.equal(previewUrl({ mime: 'image/png', image: '" onerror="x' }), null);
	assert.equal(previewUrl({ mime: 'image/png' }), null);
});

test('nodeProgress counts finished nodes and finds the running one', () => {
	const data = {
		prompt_id: 'p',
		nodes: {
			'1': { state: 'finished', value: 1, max: 1 },
			'2': { state: 'finished' },
			'3': { state: 'running', value: 4, max: 20 },
			'4': { state: 'pending' },
			junk: 5
		}
	};
	assert.deepEqual(nodeProgress(data), { done: 2, total: 4, running: '3' });
	assert.equal(nodeProgress({ nodes: {} }), null);
	assert.equal(nodeProgress({}), null);
});
