// Undo/redo for immutable snapshots (the layout model never mutates a doc, so
// storing references is safe). Usage: `h.reset(initial)`, then `h.push(next)`
// after every edit; `h.undo()` / `h.redo()` return the snapshot to show, or
// null when there is nothing to do.
const MAX = 100;

export class History<T> {
	/** Snapshots, oldest first; `index` is the current one. */
	private stack = $state.raw<T[]>([]);
	private index = $state(-1);

	/** The current snapshot, or null before `reset`. */
	get current(): T | null {
		return this.stack[this.index] ?? null;
	}
	get canUndo(): boolean {
		return this.index > 0;
	}
	get canRedo(): boolean {
		return this.index >= 0 && this.index < this.stack.length - 1;
	}

	/** Start over with `initial` as the only snapshot (e.g. after load or save-reload). */
	reset(initial: T): void {
		this.stack = [initial];
		this.index = 0;
	}

	/** Record a new snapshot, dropping any redo tail. No-op if identical to current. */
	push(next: T): void {
		if (next === this.current) return;
		const kept = this.stack.slice(Math.max(0, this.index + 1 - (MAX - 1)), this.index + 1);
		this.stack = [...kept, next];
		this.index = this.stack.length - 1;
	}

	undo(): T | null {
		if (!this.canUndo) return null;
		this.index -= 1;
		return this.stack[this.index];
	}

	redo(): T | null {
		if (!this.canRedo) return null;
		this.index += 1;
		return this.stack[this.index];
	}
}
