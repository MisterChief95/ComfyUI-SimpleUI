export type WalkFolder = { path: string; name: string; count: number };
export type WalkCursor = { group: string; position: string | null };

// Only leaves participate: walking never descends into another branch or month.
export function isLeaf(path: string): boolean {
	return /^(Favorites|Videos|Unsorted|(?:Workflow|Collections)\/[^/]+|Date\/\d{4}\/\d{2}\/\d{2})$/.test(path);
}

export function sibling(folders: WalkFolder[], path: string, delta: -1 | 1): WalkFolder | null {
	const leaves = folders.filter((folder) => isLeaf(folder.path));
	const index = leaves.findIndex((folder) => folder.path === path);
	return index < 0 ? null : leaves[index + delta] ?? null;
}

export function advance(folders: WalkFolder[], group: string, position: string | null): WalkCursor | null {
	if (position) return { group, position };
	const next = sibling(folders, group, 1);
	return next ? { group: next.path, position: null } : null;
}
