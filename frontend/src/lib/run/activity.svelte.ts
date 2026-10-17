// Global generation activity for the nav badge: one light poll of the active list
// (the call the queue panel makes), only while signed in and the tab is visible.
// Sampler percent needs the websocket and stays on the run page.
import { api } from '$lib/api';
import type { GenerationInfo, Page } from '$lib/contracts';
import { summarize, type Activity } from './activity';

const POLL_MS = 4000;

class ActivityState {
	activity = $state<Activity>({ running: 0, waiting: 0 });
	private timer: ReturnType<typeof setInterval> | undefined;
	private generation = 0;
	private inflight = false;

	/** Begin polling for one profile; returns the stop function. */
	start(): () => void {
		const mine = ++this.generation;
		this.activity = { running: 0, waiting: 0 };
		const tick = (): void => {
			if (document.visibilityState === 'visible') void this.refresh(mine);
		};
		tick();
		this.timer = setInterval(tick, POLL_MS);
		document.addEventListener('visibilitychange', tick);
		return () => {
			document.removeEventListener('visibilitychange', tick);
			clearInterval(this.timer);
			if (this.generation === mine) this.generation += 1;
			this.activity = { running: 0, waiting: 0 };
		};
	}

	private async refresh(mine: number): Promise<void> {
		if (this.inflight) return;
		this.inflight = true;
		try {
			const page = await api<Page<GenerationInfo>>('/generations?status=active&limit=200');
			if (this.generation === mine) this.activity = summarize(page.items);
		} catch {
			// A failed poll keeps the last counts; the run page reports real errors.
		} finally {
			this.inflight = false;
		}
	}
}

export const activityState = new ActivityState();
