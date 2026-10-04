/** One advance at a time; disposal/backgrounding cancels even an in-flight advance. */
export function startSlideshow(
	advance: () => Promise<boolean>,
	seconds: number,
	stop: () => void
): () => void {
	let active = true;
	let timer: ReturnType<typeof setTimeout>;
	const dispose = () => {
		active = false;
		clearTimeout(timer);
		document.removeEventListener('visibilitychange', visibility);
	};
	const visibility = () => {
		if (document.hidden) {
			dispose();
			stop();
		}
	};
	const schedule = () => {
		timer = setTimeout(async () => {
			if (!active) return;
			try {
				const moved = await advance();
				if (!active) return;
				if (moved) schedule();
				else {
					dispose();
					stop();
				}
			} catch {
				if (active) {
					dispose();
					stop();
				}
			}
		}, seconds * 1000);
	};
	document.addEventListener('visibilitychange', visibility);
	if (document.hidden) visibility();
	else schedule();
	return dispose;
}
