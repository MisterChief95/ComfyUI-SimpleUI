// Same-origin API access. In development Vite proxies /api to FastAPI; in
// production FastAPI serves this bundle, so the origin is identical.
import type { ApiError, ErrorEnvelope } from './contracts';

export class ApiRequestError extends Error {
	constructor(
		readonly status: number,
		readonly detail: ApiError
	) {
		super(detail.message);
		this.name = 'ApiRequestError';
	}
}

/** A short message safe to show a user, for any rejection api() can produce. */
export function describeApiError(cause: unknown): string {
	return cause instanceof ApiRequestError ? cause.detail.message : 'The backend is unreachable.';
}

const SAFE_METHODS = new Set(['GET', 'HEAD']);

// The double-submit CSRF value from the current session's SessionInfo. Set by
// lib/session.svelte.ts whenever a SessionInfo response arrives (login,
// logout, or the initial GET /api/session); null for the implicit
// single-user session, which auth/routes.py never asks for a token.
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
	csrfToken = token;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
	const method = (init?.method ?? 'GET').toUpperCase();
	const headers: Record<string, string> = {
		accept: 'application/json',
		...(init?.headers as Record<string, string> | undefined)
	};
	if (!SAFE_METHODS.has(method) && csrfToken) {
		headers['x-csrf-token'] = csrfToken;
	}
	const response = await fetch(`/api${path}`, {
		credentials: 'same-origin',
		...init,
		headers
	});
	const body = await response.json().catch(() => null);
	if (!response.ok) {
		const envelope = body as ErrorEnvelope | null;
		throw new ApiRequestError(
			response.status,
			envelope?.error ?? {
				code: 'internal',
				message: `Request failed (${response.status})`,
				request_id: response.headers.get('x-request-id') ?? 'unknown',
				details: []
			}
		);
	}
	return body as T;
}
