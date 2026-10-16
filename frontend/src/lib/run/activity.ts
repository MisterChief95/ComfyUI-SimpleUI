// Counts for the nav activity badge, from the same active-generation list the queue panel uses.
import type { GenerationInfo } from '../contracts.ts';

export interface Activity {
	running: number;
	waiting: number;
}

export function summarize(items: Pick<GenerationInfo, 'status'>[]): Activity {
	const running = items.filter((item) => item.status === 'running').length;
	return { running, waiting: items.length - running };
}

/** Badge text ("3") and a screen-reader description, or null when nothing is active. */
export function badge(activity: Activity): { text: string; label: string } | null {
	const total = activity.running + activity.waiting;
	if (total === 0) return null;
	const parts = [];
	if (activity.running) parts.push(`${activity.running} running`);
	if (activity.waiting) parts.push(`${activity.waiting} waiting`);
	return { text: total > 99 ? '99+' : String(total), label: parts.join(', ') };
}
