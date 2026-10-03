export type WalkFolder = { path: string; name: string; count: number };
export type WalkCursor = { group: string; position: string | null };

// Only days, workflows, and collections walk: never into another branch or month, and never
// between the unrelated top-level views (Favorites, Videos, Unsorted).
export function isLeaf(path: string): boolean {
	return /^((?:Workflow|Collections)\/[^/]+|Date\/\d{4}\/\d{2}\/\d{2})$/.test(path);
}

export function sibling(folders: WalkFolder[], path: string, delta: -1 | 1): WalkFolder | null {
	const leaves = folders.filter((folder) => isLeaf(folder.path));
	const index = leaves.findIndex((folder) => folder.path === path);
	return index < 0 ? null : (leaves[index + delta] ?? null);
}

export function advance(
	folders: WalkFolder[],
	group: string,
	position: string | null
): WalkCursor | null {
	if (position) return { group, position };
	const next = sibling(folders, group, 1);
	return next ? { group: next.path, position: null } : null;
}
