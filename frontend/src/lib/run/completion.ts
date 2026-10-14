// Run-page attention helpers: tab-title progress and the completion chime.
import { isTerminal } from './status.ts';

const DONE = new Set(['succeeded', 'completed', 'success']);

/** Document title; shows progress while a job is queued or running so a background tab is useful. */
export function pageTitle(
	name: string | null,
	status: string | null,
	progress: { value: number; max: number } | null
): string {
	const base = `${name ?? 'Generate'} · SimpleUI`;
	if (status === 'running' && progress && progress.max > 0) {
		const pct = Math.min(100, Math.floor((progress.value / progress.max) * 100));
		return `${pct}% · ${base}`;
	}
	if (status === 'running') return `Running · ${base}`;
	if (status === 'queued' || status === 'submitting') return `Queued · ${base}`;
	return base;
}

/** True only when a generation we were already following just succeeded (not when one is first loaded). */
export function justSucceeded(
	previous: { id: string; status: string } | null,
	next: { id: string; status: string } | null
): boolean {
	return (
		previous !== null &&
		next !== null &&
		previous.id === next.id &&
		!isTerminal(previous.status) &&
		DONE.has(next.status)
	);
}

/** A short two-note chime; silent if the browser blocks audio before any user interaction. */
export function playCompletionSound(): void {
	try {
		const Context = window.AudioContext;
		const audio = new Context();
		const start = audio.currentTime;
		[660, 880].forEach((frequency, index) => {
			const oscillator = audio.createOscillator();
			const gain = audio.createGain();
			oscillator.frequency.value = frequency;
			gain.gain.setValueAtTime(0.0001, start + index * 0.15);
			gain.gain.exponentialRampToValueAtTime(0.15, start + index * 0.15 + 0.02);
			gain.gain.exponentialRampToValueAtTime(0.0001, start + index * 0.15 + 0.3);
			oscillator.connect(gain).connect(audio.destination);
			oscillator.start(start + index * 0.15);
			oscillator.stop(start + index * 0.15 + 0.32);
		});
		window.setTimeout(() => void audio.close(), 800);
	} catch {
		// audio is a nicety; never break the run page
	}
}
