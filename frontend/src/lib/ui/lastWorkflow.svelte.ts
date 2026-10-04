// Remembers the last-opened workflow (per profile) so "/" and the Generate tab
// can return to it. The run page calls `lastWorkflow.set(id)` when it opens one.
import { session } from '$lib/session.svelte';
import { readStored, writeStored, removeStored } from './storage';

function keyFor(): string {
	return `simpleui:lastWorkflow:${session.info?.profile?.id ?? 'default'}`;
}

class LastWorkflow {
	/** In-memory values by storage key, so changes are reactive within a session. */
	private memory = $state<Record<string, string | null>>({});

	/** Reactive; null until a workflow was opened on this device by the current profile. */
	get id(): string | null {
		const key = keyFor();
		return key in this.memory ? this.memory[key] : readStored(key) || null;
	}

	set(id: string): void {
		const key = keyFor();
		this.memory[key] = id;
		writeStored(key, id);
	}

	clear(): void {
		const key = keyFor();
		this.memory[key] = null;
		removeStored(key);
	}

	/** Where "Generate" should go: the last run page, else the workflow picker. */
	get href(): string {
		const id = this.id;
		return id ? `/generation/${id}` : '/generation';
	}
}

export const lastWorkflow = new LastWorkflow();
