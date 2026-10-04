import { test } from 'node:test';
import assert from 'node:assert/strict';
import { api, apiJson, ApiRequestError, setCsrfToken } from './api.ts';

test('JSON mutations preserve literal types, same-origin credentials, CSRF, and API errors', async () => {
	const original = globalThis.fetch;
	const calls: { path: string; init?: RequestInit }[] = [];
	try {
		globalThis.fetch = async (path, init) => {
			calls.push({ path: String(path), init });
			return Response.json({ id: 'saved' });
		};
		setCsrfToken('session-token');
		const edits = { seed: '18446744073709551615', boolean: false, number: 4, empty: null };
		assert.deepEqual(await apiJson('/generations', 'POST', { edits }), { id: 'saved' });
		assert.equal(calls.length, 1);
		assert.equal(calls[0].path, '/api/generations');
		assert.equal(calls[0].init?.credentials, 'same-origin');
		assert.equal(calls[0].init?.method, 'POST');
		assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { edits });
		assert.deepEqual(calls[0].init?.headers, {
			accept: 'application/json',
			'content-type': 'application/json',
			'x-csrf-token': 'session-token'
		});
		await api('/session');
		assert.deepEqual(calls[1].init?.headers, { accept: 'application/json' });
		setCsrfToken(null);
		await apiJson('/settings/profile', 'PUT', { key: 'theme', value: 'dark' });
		assert.equal(new Headers(calls[2].init?.headers).has('x-csrf-token'), false);
		globalThis.fetch = async () =>
			Response.json(
				{
					error: { code: 'conflict', message: 'Changed elsewhere', request_id: 'id', details: [] }
				},
				{ status: 409 }
			);
		await assert.rejects(
			apiJson('/workflows/id/layout', 'PUT', {}),
			(cause: unknown) =>
				cause instanceof ApiRequestError &&
				cause.status === 409 &&
				cause.message === 'Changed elsewhere'
		);
	} finally {
		setCsrfToken(null);
		globalThis.fetch = original;
	}
});
