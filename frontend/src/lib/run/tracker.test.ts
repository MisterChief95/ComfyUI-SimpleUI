import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileModule } from 'svelte/compiler';
import ts from 'typescript';

// Exercise the real rune state, replacing only transport and profile settings.
const source = readFileSync(new URL('./tracker.svelte.ts', import.meta.url), 'utf8')
	.replace(
		"import { api } from '$lib/api';",
		'let request; export const mockApi = (fn) => request = fn; const api = (url) => request(url);'
	)
	.replace(
		"import { settingsState } from '$lib/settings.svelte';",
		'export const settingsState = { data: { profile: {} } };'
	);
const javascript = ts.transpileModule(source, {
	compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext }
}).outputText;
const code = compileModule(javascript, { filename: 'tracker.svelte.js', generate: 'client' })
	.js.code.replace('svelte/internal/client', import.meta.resolve('svelte/internal/client'))
	.replace('./events', new URL('./events.ts', import.meta.url).href)
	.replace('./status', new URL('./status.ts', import.meta.url).href);
const { GenerationTracker, mockApi, settingsState } = await import(
	`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
);

Object.defineProperty(globalThis, 'location', {
	value: { href: 'http://localhost/' },
	configurable: true
});
Object.defineProperty(globalThis, 'WebSocket', {
	value: class {
		close() {}
	},
	configurable: true
});
const item = (id: string, generation_id: string) => ({ id, generation_id });
const generation = (id: string, workflow_id: string, status = 'succeeded') => ({
	id,
	workflow_id,
	status,
	output_state: status === 'succeeded' ? 'ready' : 'pending'
});

test('defaults restore historical results; startup clearing keeps new session batches across navigation', async () => {
	const old = generation('old', 'workflow');
	const fresh = generation('fresh', 'workflow');
	let newest = old;
	mockApi(async (url: string) => {
		if (url.startsWith('/generations?')) return { items: [newest] };
		if (url.startsWith('/generations/')) return newest;
		if (url.includes('generation_id='))
			return { items: [item('a', newest.id), item('b', newest.id)], next_cursor: null };
		return {
			items: [item('a', newest.id), item('b', newest.id), item('older', 'earlier')],
			next_cursor: null
		};
	});
	settingsState.data.profile = {};
	const defaults = new GenerationTracker('workflow');
	try {
		await defaults.start();
		await defaults.refreshRecent();
		assert.equal(defaults.outputs.length, 2);
		assert.equal(defaults.recent.length, 3);
	} finally {
		defaults.stop();
	}

	settingsState.data.profile = { clear_generation_on_startup: true };
	const startup = new GenerationTracker('new-workflow');
	newest = generation('previous-session', 'new-workflow');
	try {
		await startup.start();
		await startup.refreshRecent();
		assert.equal(startup.latest, null);
		assert.deepEqual(startup.recent, []);
		assert.equal(startup.shown, null);
		newest = { ...fresh, workflow_id: 'new-workflow' };
		await startup.adopt(newest);
		await startup.refreshRecent();
		assert.equal(startup.outputs.length, 2);
		assert.equal(startup.recent.length, 2);
	} finally {
		startup.stop();
	}
	const revisited = new GenerationTracker('new-workflow');
	try {
		await revisited.start();
		assert.equal(revisited.outputs.length, 2);
	} finally {
		revisited.stop();
	}
});

test('Generate clearing rejects stale requests and keeps every output of the new batch', async () => {
	settingsState.data.profile = {};
	const tracker = new GenerationTracker('batch');
	tracker.latest = generation('prior', 'batch');
	tracker.outputs = [item('prior-output', 'prior')];
	tracker.recent = tracker.outputs;
	tracker.picked = tracker.outputs[0];
	tracker.pickedOutputs = tracker.outputs;
	tracker.preview = 'old preview';
	let release!: (value: unknown) => void;
	mockApi(
		() =>
			new Promise((resolve) => {
				release = resolve;
			})
	);
	const stale = tracker.refreshRecent();
	tracker.clearResults();
	assert.equal(tracker.latest, null);
	assert.equal(tracker.shown, null);
	assert.equal(tracker.preview, null);
	assert.deepEqual(tracker.pickedOutputs, []);
	release({ items: [item('prior-output', 'prior')] });
	await stale;
	assert.deepEqual(tracker.recent, []);

	const batch = [item('first', 'next'), item('second', 'next'), item('third', 'next')];
	mockApi(async (url: string) => ({
		items: url.includes('generation_id=') ? batch : [...batch, item('prior-output', 'prior')],
		next_cursor: null
	}));
	await tracker.adopt(generation('next', 'batch'));
	await tracker.refreshRecent();
	assert.deepEqual(
		tracker.outputs.map((entry: { id: string }) => entry.id),
		batch.map((entry) => entry.id)
	);
	assert.equal(tracker.recent.length, 3);
	await tracker.pick(tracker.outputs[1]);
	assert.equal(tracker.shown.id, 'second');
	const revisited = new GenerationTracker('batch');
	mockApi(async (url: string) => {
		if (url.startsWith('/generations?')) return { items: [generation('next', 'batch')] };
		if (url.startsWith('/generations/')) return generation('next', 'batch');
		return { items: [...batch, item('prior-output', 'prior')], next_cursor: null };
	});
	try {
		await revisited.start();
		await revisited.refreshRecent();
		assert.equal(revisited.recent.length, 3);
	} finally {
		revisited.stop();
	}
});

test('clearing invalidates an in-flight startup adoption', async () => {
	const tracker = new GenerationTracker('slow');
	let release!: (value: unknown) => void;
	mockApi(
		() =>
			new Promise((resolve) => {
				release = resolve;
			})
	);
	const adoption = tracker.adopt('old-slow');
	tracker.clearResults();
	release(generation('old-slow', 'slow'));
	await adoption;
	assert.equal(tracker.latest, null);
	assert.deepEqual(tracker.outputs, []);
});
