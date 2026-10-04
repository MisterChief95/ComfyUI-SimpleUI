import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileModule } from 'svelte/compiler';
import ts from 'typescript';
import { layoutGrid, visibleRows } from './grid.ts';
import { startSlideshow } from './viewerPlayback.ts';

// Compile the real rune state for node:test; only transport/settings are replaced.
let source = readFileSync(new URL('./gallery.svelte.ts', import.meta.url), 'utf8')
	.replace(
		"import { api, apiJson, describeApiError } from '$lib/api';",
		'let request; export function mockApi(fn) { request = fn; } const api = (url, init) => request(url, init); const apiJson = (url, method, value) => api(url, { method, headers: { "content-type": "application/json" }, body: JSON.stringify(value) }); const describeApiError = (error) => error.message;'
	)
	.replace(
		"import { settingsState } from '$lib/settings.svelte';",
		'const settingsState = { data: { profile: { gallery_page_size: 2 } } };'
	);
source = ts.transpileModule(source, {
	compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext }
}).outputText;
const code = compileModule(source, { filename: 'gallery.svelte.js', generate: 'client' })
	.js.code.replace('svelte/internal/client', import.meta.resolve('svelte/internal/client'))
	.replace('./walk', new URL('./walk.ts', import.meta.url).href);
const { GalleryState, mockApi } = await import(
	`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
);

test('suggestions debounce, ignore stale responses, apply a selection, and stop on disposal', async () => {
	const gallery = new GalleryState();
	const requests: string[] = [];
	let release!: (value: unknown) => void;
	mockApi((url: string) => {
		requests.push(url);
		if (url.includes('q=old'))
			return new Promise((resolve) => {
				release = resolve;
			});
		if (url.startsWith('/media/suggestions')) return Promise.resolve({ items: ['new portrait'] });
		if (url.startsWith('/media/tree')) return Promise.resolve({ children: [], breadcrumbs: [] });
		return Promise.resolve({ items: [], next_cursor: null });
	});
	const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
	try {
		gallery.setPrompt('x');
		await wait(300);
		assert.deepEqual(requests, []);
		gallery.setPrompt('ol');
		gallery.setPrompt('old');
		assert.deepEqual(requests, []);
		await wait(300);
		assert.equal(requests.length, 1);
		gallery.setPrompt('new');
		release({ items: ['old portrait'] });
		await wait(0);
		assert.deepEqual(gallery.suggestions, []);
		await wait(300);
		assert.deepEqual(gallery.suggestions, ['new portrait']);
		gallery.setPrompt('new portrait');
		await wait(0);
		assert.equal(gallery.prompt, 'new portrait');
		assert.equal(gallery.activeCount, 1);
		assert.ok(
			requests.some(
				(url) => new URL(url, 'http://localhost').searchParams.get('prompt') === 'new portrait'
			)
		);
		assert.deepEqual(gallery.suggestions, []);
		const count = requests.length;
		gallery.setPrompt('unused');
		gallery.dispose();
		await wait(300);
		assert.equal(requests.length, count);
	} finally {
		gallery.dispose();
	}
});

const days = ['03', '02', '01'].map((day) => ({
	path: `Date/2026/10/${day}`,
	name: day,
	count: 2
}));
const item = (id: string) => ({ id, generation_id: null });

test('collection filter applies across pages; mutations refresh and failures keep selection state', async () => {
	const gallery = new GalleryState();
	const requests: { url: string; init?: RequestInit }[] = [];
	let fail = false;
	mockApi(async (url: string, init?: RequestInit) => {
		requests.push({ url, init });
		if (init) {
			if (fail) throw new Error('Collection was not found.');
			return init.method === 'POST' && url === '/media/collections' ? { id: 'new' } : { ok: true };
		}
		if (url === '/media/collections') return { items: [{ id: 'a', name: 'Trips', count: 2 }] };
		if (url.startsWith('/media/tree')) return { children: [], breadcrumbs: [] };
		const cursor = new URL(url, 'http://localhost').searchParams.get('cursor');
		return { items: [item(cursor ? 'second' : 'first')], next_cursor: cursor ? null : 'next' };
	});
	try {
		gallery.collectionId = 'a';
		await gallery.apply();
		assert.equal(gallery.activeCount, 1);
		gallery.collectionId = 'draft';
		await gallery.load(false);
		const pages = requests.filter(({ url }) => url.startsWith('/media?'));
		assert.ok(
			pages.every(
				({ url }) => new URL(url, 'http://localhost').searchParams.get('collection_id') === 'a'
			)
		);
		assert.equal(gallery.collections[0].name, 'Trips');
		assert.equal((await gallery.changeCollection('POST', '', { name: 'New' }))?.id, 'new');
		await gallery.changeCollection('POST', 'new/media', { ids: ['first', 'second'] });
		assert.deepEqual(
			JSON.parse(
				requests.find(({ url }) => url === '/media/collections/new/media')!.init!.body as string
			),
			{ ids: ['first', 'second'] }
		);
		fail = true;
		assert.equal(await gallery.changeCollection('POST', 'a/remove', { ids: ['first'] }), null);
		assert.equal(gallery.error, 'Collection was not found.');
		assert.equal(gallery.collectionBusy, false);
		fail = false;
		gallery.collectionId = 'a';
		await gallery.navigate('Collections/a');
		assert.equal(gallery.currentCollection, 'a');
		await gallery.changeCollection('DELETE', 'a');
		assert.equal(gallery.folderPath, 'Collections');
		assert.equal(gallery.collectionId, '');
		assert.equal(gallery.activeCount, 0);
		await gallery.resetFilters();
	} finally {
		gallery.dispose();
	}
});

test('field choice applies with the term, follows paging, and resets without a filter badge', async () => {
	const { gallery, requests } = setup(
		{
			':': { items: [item('a')], next_cursor: 'page2' },
			':page2': { items: [item('b')], next_cursor: null }
		},
		false
	);
	try {
		gallery.prompt = 'checkpoint';
		gallery.setSearchField('model');
		await gallery.apply();
		assert.equal(gallery.activeCount, 1);
		gallery.setSearchField('prompt'); // Draft changes must not alter the current cursor's filter.
		await gallery.load(false);
		assert.ok(requests.every((query) => query.get('search_field') === 'model'));
		await gallery.apply();
		assert.equal(requests.at(-1)?.get('search_field'), 'prompt');
		assert.equal(requests.at(-1)?.get('cursor'), null);
		await gallery.resetFilters();
		assert.equal(gallery.searchField, 'any');
		assert.equal(gallery.activeCount, 0);
		assert.equal(requests.at(-1)?.get('search_field'), null);
	} finally {
		gallery.dispose();
	}
});

function setup(
	pages: Record<string, { items: ReturnType<typeof item>[]; next_cursor: string | null }>,
	walk = true
) {
	const requests: URLSearchParams[] = [];
	mockApi(async (url: string) => {
		if (url === '/media/collections') return { items: [] };
		const query = new URL(url, 'http://localhost').searchParams;
		const path = query.get('path') ?? '';
		if (url.startsWith('/media/tree'))
			return { path, children: path === 'Date/2026/10' ? days : [], breadcrumbs: [] };
		requests.push(query);
		return pages[`${path}:${query.get('cursor') ?? ''}`] ?? { items: [], next_cursor: null };
	});
	const prefs = { walk, sort: 'random' };
	return { gallery: new GalleryState(prefs), requests, prefs };
}

test('grid and Viewer exhaust a group cursor, skip filtered leaves, and cross in both directions', async () => {
	const { gallery, requests } = setup({
		[`${days[0].path}:`]: { items: [item('a')], next_cursor: 'page2' },
		[`${days[0].path}:page2`]: { items: [item('b')], next_cursor: null },
		[`${days[2].path}:`]: { items: [item('c')], next_cursor: null }
	});
	gallery.prompt = 'cat';
	await gallery.apply();
	await gallery.navigate(days[0].path);
	await gallery.select(gallery.items[0]);
	await gallery.step(1);
	assert.equal(gallery.selected.id, 'b');
	await gallery.step(1);
	assert.equal(gallery.selected.id, 'c');
	assert.deepEqual(
		gallery.items.map((entry: { id: string }) => entry.id),
		['a', 'b', 'c']
	);
	assert.equal(gallery.groupHeader(0), days[0].path);
	assert.equal(gallery.groupHeader(1), null);
	assert.equal(gallery.groupHeader(2), days[2].path);
	assert.equal(gallery.canLoadMore, false);
	await gallery.step(-1);
	assert.equal(gallery.selected.id, 'b');
	const leafRequests = requests.filter((query) => query.get('path'));
	assert.deepEqual(
		leafRequests.map((query) => [query.get('path'), query.get('cursor')]),
		[
			[days[0].path, null],
			[days[0].path, 'page2'],
			[days[1].path, null],
			[days[2].path, null]
		]
	);
	assert.ok(
		leafRequests.every((query) => query.get('prompt') === 'cat' && query.get('sort') === 'random')
	);
});

test('previous from the initial leaf finishes the preceding leaf and selects its last item', async () => {
	const { gallery } = setup({
		[`${days[0].path}:`]: { items: [item('a')], next_cursor: 'page2' },
		[`${days[0].path}:page2`]: { items: [item('b')], next_cursor: null },
		[`${days[2].path}:`]: { items: [item('c')], next_cursor: null }
	});
	await gallery.navigate(days[2].path);
	await gallery.select(gallery.items[0]);
	await gallery.step(-1);
	assert.equal(gallery.selected.id, 'b');
	assert.deepEqual(
		gallery.items.map((entry: { id: string }) => entry.id),
		['a', 'b', 'c']
	);
	assert.equal(gallery.canLoadPrevious, false);
	assert.equal(gallery.canLoadMore, false);
	await gallery.step(1);
	assert.equal(gallery.selected.id, 'c');
});

test('Walk off stops at the folder boundary; toggling/resetting discards prior continuation', async () => {
	const { gallery, requests, prefs } = setup(
		{ [`${days[0].path}:`]: { items: [item('a')], next_cursor: null } },
		false
	);
	await gallery.navigate(days[0].path);
	assert.equal(gallery.canLoadMore, false);
	assert.equal(gallery.canLoadPrevious, false);
	await gallery.load(false);
	assert.equal(requests.length, 1);
	prefs.walk = true;
	await gallery.load(true);
	assert.equal(gallery.canLoadMore, true);
	prefs.walk = false;
	await gallery.load(true);
	assert.equal(gallery.canLoadMore, false);
	await gallery.navigate(days[1].path);
	assert.deepEqual(gallery.items, []);
	assert.equal(gallery.continuation, null);
});

test('filter replacements keep existing tiles while pending or failed and publish only ready results', async () => {
	const { gallery } = setup({ ':': { items: [item('a'), item('b')], next_cursor: null } }, false);
	await gallery.load();
	const original = gallery.items;
	let release!: (page: unknown) => void;
	let reject!: (error: Error) => void;
	let requested!: () => void;
	const pageRequested = new Promise<void>((resolve) => {
		requested = resolve;
	});
	mockApi((url: string) => {
		if (url === '/media/collections') return Promise.resolve({ items: [] });
		if (url.startsWith('/media/tree')) return Promise.resolve({ children: [], breadcrumbs: [] });
		requested();
		return new Promise((resolve, fail) => {
			release = resolve;
			reject = fail;
		});
	});
	gallery.favorite = 'true';
	const pending = gallery.apply();
	await pageRequested;
	assert.equal(gallery.items, original);
	assert.equal(gallery.loading, true);
	reject(new Error('offline'));
	await pending;
	assert.equal(gallery.items, original);
	assert.equal(gallery.error, 'offline');
	const retry = gallery.load();
	// Allow the already-resolved tree/collection requests to reach the media request.
	await new Promise((resolve) => setTimeout(resolve, 0));
	assert.equal(gallery.items, original);
	release({ items: [item('b'), item('c')], next_cursor: null });
	await retry;
	assert.deepEqual(
		gallery.items.map((entry: { id: string }) => entry.id),
		['b', 'c']
	);
	assert.equal(gallery.loading, false);
	assert.equal(gallery.error, null);
	gallery.dispose();
});

test('folder replacements preserve tiles and tree until a successful response', async () => {
	const { gallery } = setup({ ':': { items: [item('a'), item('b')], next_cursor: null } }, false);
	await gallery.load();
	const original = gallery.items;
	const originalTree = gallery.tree;
	let release!: (page: unknown) => void;
	let reject!: (error: Error) => void;
	let requested!: () => void;
	let pageRequested = new Promise<void>((resolve) => (requested = resolve));
	mockApi((url: string) => {
		if (url === '/media/collections') return Promise.resolve({ items: [] });
		if (url.startsWith('/media/tree'))
			return Promise.resolve({ path: 'Workflow', children: [], breadcrumbs: [] });
		requested();
		return new Promise((resolve, fail) => {
			release = resolve;
			reject = fail;
		});
	});
	const pending = gallery.navigate('Workflow');
	await pageRequested;
	assert.equal(gallery.items, original);
	assert.equal(gallery.tree, originalTree);
	assert.equal(gallery.loading, true);
	reject(new Error('offline'));
	await pending;
	assert.equal(gallery.items, original);
	assert.equal(gallery.tree, originalTree);
	assert.equal(gallery.error, 'offline');
	pageRequested = new Promise<void>((resolve) => (requested = resolve));
	const retry = gallery.navigate('Workflow');
	await pageRequested;
	assert.equal(gallery.items, original);
	release({ items: [item('b'), item('c')], next_cursor: null });
	await retry;
	assert.deepEqual(
		gallery.items.map((entry: { id: string }) => entry.id),
		['b', 'c']
	);
	assert.equal(gallery.tree.path, 'Workflow');
	assert.equal(gallery.loading, false);
	assert.equal(gallery.error, null);
	// An empty branch still publishes matching metadata, without clearing while pending.
	pageRequested = new Promise<void>((resolve) => (requested = resolve));
	const empty = gallery.navigate('Workflow');
	await pageRequested;
	assert.equal(gallery.items.length, 2);
	release({ items: [], next_cursor: null });
	await empty;
	assert.deepEqual(gallery.items, []);
	assert.equal(gallery.tree.path, 'Workflow');
});

test('failed sibling loads preserve the group for an explicit retry', async () => {
	const { gallery } = setup({ [`${days[0].path}:`]: { items: [item('a')], next_cursor: null } });
	await gallery.navigate(days[0].path);
	await gallery.select(gallery.items[0]);
	mockApi(async () => {
		throw new Error('offline');
	});
	await gallery.step(1);
	assert.equal(gallery.error, 'offline');
	assert.equal(gallery.selected.id, 'a');
	assert.deepEqual(gallery.continuation, { group: days[1].path, position: null });
	mockApi(async () => ({ items: [item('b')], next_cursor: null }));
	await gallery.step(1);
	assert.equal(gallery.selected.id, 'b');
	assert.equal(gallery.error, null);
});

test('navigation during a boundary request discards the stale response', async () => {
	const { gallery } = setup({ [`${days[0].path}:`]: { items: [item('a')], next_cursor: null } });
	await gallery.navigate(days[0].path);
	let release!: (page: unknown) => void;
	const pending = new Promise((resolve) => {
		release = resolve;
	});
	mockApi(() => pending);
	const loading = gallery.load(false);
	await gallery.navigate(days[2].path);
	assert.equal(gallery.items[0].id, 'a');
	mockApi(async (url: string) =>
		url.startsWith('/media/tree')
			? { children: [], breadcrumbs: [] }
			: { items: [item('c')], next_cursor: null }
	);
	release({ items: [item('stale')], next_cursor: null });
	await loading;
	assert.deepEqual(
		gallery.items.map((entry: { id: string }) => entry.id),
		['c']
	);
	assert.equal(gallery.folderPath, days[2].path);
});

test('empty starting leaves advance and duplicate media never creates repeated grid keys', async () => {
	const { gallery } = setup({
		[`${days[1].path}:`]: { items: [item('b')], next_cursor: null },
		[`${days[2].path}:`]: { items: [item('b'), item('c')], next_cursor: null }
	});
	await gallery.navigate(days[0].path);
	assert.deepEqual(gallery.continuation, { group: days[2].path, position: null });
	await gallery.load(false);
	assert.deepEqual(
		gallery.items.map((entry: { id: string }) => entry.id),
		['b', 'c']
	);
});

test('100k items keep the rendered window bounded on desktop and phone', () => {
	for (const width of [343, 1024]) {
		const grid = layoutGrid(100_000, width, 160, 8);
		for (const top of [0, grid.height / 2, grid.height - 800]) {
			const rows = visibleRows(grid.rows, top, 800);
			const tiles = rows.reduce((count, row) => count + row.end - row.start, 0);
			assert.ok(tiles <= 100, `${width}px: ${tiles} tiles`);
			assert.ok(rows.length <= 15);
		}
		assert.equal(grid.rows.at(-1)?.end, 100_000);
	}
});

test('group headers flush incomplete rows and retain each item exactly once', () => {
	const grid = layoutGrid(
		7,
		500,
		160,
		8,
		(index) => ({ 0: 'first', 2: 'second', 6: 'third' })[index] ?? null
	);
	assert.deepEqual(
		grid.rows.map((row) => [row.start, row.end, row.group]),
		[
			[0, 0, 'first'],
			[0, 2, null],
			[2, 2, 'second'],
			[2, 5, null],
			[5, 6, null],
			[6, 6, 'third'],
			[6, 7, null]
		]
	);
	assert.equal(grid.height, grid.rows.at(-1)!.top + grid.cell);
	assert.deepEqual(
		visibleRows(grid.rows, 0, 1, 0).map((row) => row.group),
		['first']
	);
	assert.equal(layoutGrid(0, 343, 400, 8).height, 0);
	assert.equal(layoutGrid(1, 343, 400, 8).cell, 343);
});

test('slideshow cancels on close/background, serializes advances and stops at the end', async (context) => {
	context.mock.timers.enable({ apis: ['setTimeout'] });
	const original = Object.getOwnPropertyDescriptor(globalThis, 'document');
	const page = Object.assign(new EventTarget(), { hidden: false });
	Object.defineProperty(globalThis, 'document', { configurable: true, value: page });
	let calls = 0;
	let stopped = 0;
	let finish!: (moved: boolean) => void;
	const advance = () => {
		calls++;
		return new Promise<boolean>((resolve) => {
			finish = resolve;
		});
	};
	const stop = () => {
		stopped++;
	};
	const flush = async () => {
		await Promise.resolve();
		await Promise.resolve();
	};
	try {
		const close = startSlideshow(advance, 5, stop);
		context.mock.timers.tick(4999);
		assert.equal(calls, 0);
		context.mock.timers.tick(1);
		assert.equal(calls, 1);
		context.mock.timers.tick(10000);
		assert.equal(calls, 1, 'no overlapping advance');
		close();
		finish(true);
		await flush();
		context.mock.timers.tick(10000);
		assert.equal(calls, 1, 'close cancels an in-flight reschedule');
		startSlideshow(advance, 2, stop);
		context.mock.timers.tick(2000);
		page.hidden = true;
		page.dispatchEvent(new Event('visibilitychange'));
		finish(true);
		await flush();
		context.mock.timers.tick(10000);
		assert.equal(calls, 2);
		assert.equal(stopped, 1);
		page.hidden = false;
		startSlideshow(advance, 2, stop);
		context.mock.timers.tick(2000);
		finish(false);
		await flush();
		context.mock.timers.tick(10000);
		assert.equal(calls, 3);
		assert.equal(stopped, 2, 'the last slide stops playback');
		page.hidden = true;
		startSlideshow(advance, 2, stop);
		context.mock.timers.tick(10000);
		assert.equal(calls, 3, 'background start schedules nothing');
	} finally {
		if (original) Object.defineProperty(globalThis, 'document', original);
		else Reflect.deleteProperty(globalThis, 'document');
	}
});
