// The prompt box that trigger-word chips insert into: the one most recently
// focused, else the first one mounted. A plain module slot, not a store, because
// only ModelInfo reads it and only on click.
let target: ((text: string) => void) | null = null;

/** Register a prompt inserter; returns the unregister function. */
export function registerPrompt(insert: (text: string) => void, focused = false): () => void {
	if (focused || !target) target = insert;
	return () => {
		if (target === insert) target = null;
	};
}

export function insertIntoPrompt(text: string): boolean {
	target?.(text);
	return target !== null;
}
