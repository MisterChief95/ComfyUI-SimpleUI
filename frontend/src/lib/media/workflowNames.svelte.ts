// Workflow id -> name, shared by the gallery filter and history cards.
import { api } from '$lib/api';
import type { Page, WorkflowInfo } from '$lib/contracts';

class WorkflowNames {
	list = $state.raw<WorkflowInfo[]>([]);
	private loaded = false;

	/** One GET per page load is plenty; failures just leave raw ids/empty select. */
	async load(): Promise<void> {
		if (this.loaded) return;
		this.loaded = true;
		try {
			let cursor: string | null = null;
			const all: WorkflowInfo[] = [];
			// ponytail: capped at 10 pages of 200 workflows to avoid a runaway loop.
			for (let i = 0; i < 10; i += 1) {
				const page: Page<WorkflowInfo> = await api(
					`/workflows?limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`
				);
				all.push(...page.items);
				cursor = page.next_cursor;
				if (!cursor) break;
			}
			this.list = all;
		} catch {
			this.loaded = false;
		}
	}

	name(id: string | null): string | null {
		return this.list.find((workflow) => workflow.id === id)?.name ?? null;
	}
}

export const workflowNames = new WorkflowNames();
