/** Absolute local date/time for tooltips and details. */
export function formatDate(value: number | string): string {
	return new Date(Number(value)).toLocaleString();
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", then a plain date. */
export function relativeTime(value: number | string, now = Date.now()): string {
	const seconds = Math.round((now - Number(value)) / 1000);
	if (seconds < 45) return 'just now';
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes} min ago`;
	const hours = Math.round(minutes / 60);
	if (hours < 24) return `${hours} h ago`;
	const days = Math.round(hours / 24);
	if (days < 8) return `${days} d ago`;
	return new Date(Number(value)).toLocaleDateString();
}
