// Generation status vocabulary (backend/app/generations) and error summaries.
import type { GenerationInfo } from '../contracts.ts';

const TERMINAL = new Set([
	'succeeded',
	'completed',
	'success',
	'failed',
	'cancelled',
	'interrupted',
	'unknown'
]);

export function isTerminal(status: string): boolean {
	return TERMINAL.has(status);
}

export type StatusKind = 'accent' | 'success' | 'warning' | 'danger' | 'neutral';

export function statusInfo(generation: GenerationInfo | null): { label: string; kind: StatusKind } {
	if (!generation) return { label: 'Idle', kind: 'neutral' };
	switch (generation.status) {
		case 'submitting':
			return { label: 'Submitting', kind: 'warning' };
		case 'submission_unknown':
			return { label: 'Checking…', kind: 'warning' };
		case 'queued':
			return { label: 'Queued', kind: 'warning' };
		case 'running':
			return { label: 'Running', kind: 'accent' };
		case 'succeeded':
		case 'completed':
		case 'success':
			if (generation.output_state === 'partial')
				return { label: 'Done (partial)', kind: 'warning' };
			if (generation.output_state === 'unavailable')
				return { label: 'Done, no output', kind: 'warning' };
			return { label: 'Done', kind: 'success' };
		case 'failed':
			return { label: 'Failed', kind: 'danger' };
		case 'cancelled':
		case 'interrupted':
			return { label: 'Cancelled', kind: 'neutral' };
		default:
			return { label: 'Unknown', kind: 'warning' };
	}
}

type Loose = Record<string, unknown>;

const isObject = (value: unknown): value is Loose =>
	typeof value === 'object' && value !== null && !Array.isArray(value);

function execution(data: unknown): string | null {
	if (!isObject(data)) return null;
	const message = data.exception_message ?? data.message;
	if (typeof message !== 'string' || !message.trim()) return null;
	const where = data.node_type
		? ` (${String(data.node_type)}${data.node_id ? ` #${String(data.node_id)}` : ''})`
		: '';
	return `${message.trim()}${where}`;
}

/** A short human summary of a generation's error blob; the raw JSON stays in `details`. */
export function describeGenerationError(
	error: Loose | null
): { summary: string[]; details: string } | null {
	if (!error) return null;
	const summary: string[] = [];
	const add = (text: string | null): void => {
		if (text && !summary.includes(text)) summary.push(text);
	};
	add(execution(error.execution));
	if (Array.isArray(error.history_errors))
		for (const item of error.history_errors) add(execution(item));
	if (isObject(error.node_errors)) {
		for (const [nodeId, entry] of Object.entries(error.node_errors)) {
			if (!isObject(entry) || !Array.isArray(entry.errors)) continue;
			for (const detail of entry.errors) {
				if (!isObject(detail) || typeof detail.message !== 'string') continue;
				const extra =
					typeof detail.details === 'string' && detail.details ? `: ${detail.details}` : '';
				add(`${detail.message}${extra} (${String(entry.class_type ?? 'node')} #${nodeId})`);
			}
		}
	}
	if (isObject(error.output_capture) && typeof error.output_capture.message === 'string') {
		add(`Saving the output failed: ${error.output_capture.message}`);
	}
	if (isObject(error.submission)) add('ComfyUI did not accept the job.');
	if (summary.length === 0) summary.push('The generation reported an error.');
	return { summary, details: JSON.stringify(error, null, 2) };
}
