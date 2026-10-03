// Pure helpers for /api/events frames (no Svelte, runs under `npm test`).
// Frame shape: { generation_id, type, data } (docs/API.md "Generation events").

export interface Frame {
	generation_id: string;
	type: string;
	data: Record<string, unknown>;
}

const isObject = (value: unknown): value is Record<string, unknown> =>
	typeof value === 'object' && value !== null && !Array.isArray(value);

/** A well-formed frame, or null for anything else (events are advisory; never throw). */
export function parseFrame(raw: unknown): Frame | null {
	try {
		const message: unknown = JSON.parse(String(raw));
		if (!isObject(message) || typeof message.generation_id !== 'string' || typeof message.type !== 'string') {
			return null;
		}
		return { generation_id: message.generation_id, type: message.type, data: isObject(message.data) ? message.data : {} };
	} catch {
		return null;
	}
}

/** A `preview` frame as an <img src> data URL; null unless it is a plain base64 image. */
export function previewUrl(data: Record<string, unknown>): string | null {
	const { mime, image } = data;
	if (typeof image !== 'string' || !/^[A-Za-z0-9+/]+={0,2}$/.test(image)) return null;
	return mime === 'image/jpeg' || mime === 'image/png' ? `data:${mime};base64,${image}` : null;
}

export interface NodeProgress {
	/** Nodes finished (executed or cached). */
	done: number;
	total: number;
	/** The node currently running, if any. */
	running: string | null;
}

/** Overall picture from a `progress_state` frame: how many of the graph's nodes are finished. */
export function nodeProgress(data: Record<string, unknown>): NodeProgress | null {
	if (!isObject(data.nodes)) return null;
	let done = 0;
	let total = 0;
	let running: string | null = null;
	for (const [id, node] of Object.entries(data.nodes)) {
		if (!isObject(node)) continue;
		total += 1;
		if (node.state === 'finished') done += 1;
		else if (node.state === 'running' && running === null) running = id;
	}
	return total > 0 ? { done, total, running } : null;
}
