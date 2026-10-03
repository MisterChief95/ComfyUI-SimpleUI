export interface ValueDiff {
	key: string;
	a: string;
	b: string;
	same: boolean;
}

const show = (v: unknown): string =>
	v === undefined ? '—' : typeof v === 'string' ? v : JSON.stringify(v);

/** Row per effective-value key present in either generation; differing rows first. */
export function diffValues(
	a: Record<string, unknown> | null,
	b: Record<string, unknown> | null
): ValueDiff[] {
	const keys = [...new Set([...Object.keys(a ?? {}), ...Object.keys(b ?? {})])].sort();
	const rows = keys.map((key) => {
		const x = show(a?.[key]);
		const y = show(b?.[key]);
		return { key, a: x, b: y, same: x === y };
	});
	return [...rows.filter((r) => !r.same), ...rows.filter((r) => r.same)];
}

/** Reuse A/B only makes sense when both generations came from the same workflow and still have saved values. */
export function canReuseBoth(
	a: { workflow_id: string | null; effective_values?: unknown } | null,
	b: typeof a
): boolean {
	return (
		!!a?.workflow_id &&
		a.workflow_id === b?.workflow_id &&
		!!a.effective_values &&
		!!b?.effective_values
	);
}
