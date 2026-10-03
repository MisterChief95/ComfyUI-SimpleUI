import { tick } from 'svelte';
import { prefersReducedMotion } from 'svelte/motion';

let opening = false;

/** Match the clicked image to the dialog image, including its position and crop. */
export async function openViewer(source: HTMLImageElement | null, show: () => void): Promise<void> {
	if (opening) return;
	if (!source?.naturalWidth || prefersReducedMotion.current || !document.startViewTransition) {
		show();
		return;
	}
	opening = true;
	const originalName = source.style.viewTransitionName;
	let decodeTimeout: ReturnType<typeof setTimeout> | undefined;
	document.documentElement.classList.add('opening-media');
	source.style.viewTransitionName = 'media-zoom';
	const transition = document.startViewTransition(async () => {
		source.style.viewTransitionName = originalName;
		show();
		await tick();
		const destination = document.querySelector<HTMLImageElement>(
			'dialog[open] [data-lightbox-image]'
		);
		if (!destination) {
			transition.skipTransition();
			return;
		}
		// Do not freeze the page waiting for a slow or missing original file.
		await Promise.race([
			destination.decode().catch(() => {}),
			new Promise<void>((resolve) => (decodeTimeout = setTimeout(resolve, 150)))
		]);
		if (!destination.isConnected || !destination.complete || !destination.naturalWidth) {
			transition.skipTransition();
			return;
		}
		destination.style.viewTransitionName = 'media-zoom';
	});
	// A hidden tab or interrupted capture may skip animation; the dialog still opens.
	void transition.ready.catch(() => {});
	try {
		await transition.finished;
	} finally {
		clearTimeout(decodeTimeout);
		source.style.viewTransitionName = originalName;
		const destination = document.querySelector<HTMLImageElement>(
			'dialog[open] [data-lightbox-image]'
		);
		if (destination) destination.style.viewTransitionName = '';
		document.documentElement.classList.remove('opening-media');
		opening = false;
	}
}
