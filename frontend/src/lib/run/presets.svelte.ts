// Presets of one workflow for the run page: list, apply, save the current
// draft, overwrite, rename, delete. A 409 (changed elsewhere, or the 100-preset
// cap) always ends in a refetch so the list shows the truth.
import { api, ApiRequestError, describeApiError } from '$lib/api';
import type { Preset } from '$lib/contracts';
import { describeApply, planApply, presetValues } from './presets';
import type { RunState } from './run.svelte';

const JSON_HEADERS = { 'content-type': 'application/json' };

export class PresetsState {
	items = $state<Preset[]>([]);
	loading = $state(false);
	/** Preset id being changed, or 'new'. */
	busy = $state<string | null>(null);
	error = $state<string | null>(null);
	/** Outcome of the last apply/save, shown on the run page toolbar. */
	message = $state<string | null>(null);

	private readonly run: RunState;
	private get base(): string {
		return `/workflows/${this.run.workflowId}/presets`;
	}

	constructor(run: RunState) {
		this.run = run;
	}

	async load(): Promise<void> {
		this.loading = true;
		try {
			this.items = await api<Preset[]>(this.base);
		} catch (cause) {
			this.error = describeApiError(cause);
		} finally {
			this.loading = false;
		}
	}

	/** What "Save current" would store. */
	get currentValues() {
		return presetValues(this.run.schema?.controls ?? [], this.run.draft);
	}

	apply(preset: Preset): void {
		const hidden = new Set(this.run.resolved?.hidden.map((control) => control.binding_id));
		const plan = planApply(this.run.schema?.controls ?? [], hidden, preset.values);
		this.run.replaceDraft(plan.draft);
		this.message = describeApply(preset.name, plan);
		this.error = null;
	}

	create(name: string): Promise<boolean> {
		return this.mutate('new', async () => {
			await api<Preset>(this.base, {
				method: 'POST',
				headers: JSON_HEADERS,
				body: JSON.stringify({ name, values: this.currentValues })
			});
			const n = Object.keys(this.currentValues).length;
			this.message = `Saved preset "${name}" (${n} changed value${n === 1 ? '' : 's'}).`;
		});
	}

	overwrite(preset: Preset): Promise<boolean> {
		return this.update(
			preset,
			{ values: this.currentValues },
			`Preset "${preset.name}" overwritten with the current values.`
		);
	}

	rename(preset: Preset, name: string): Promise<boolean> {
		return this.update(preset, { name }, null);
	}

	remove(preset: Preset): Promise<boolean> {
		return this.mutate(preset.id, async () => {
			await api(`${this.base}/${preset.id}`, { method: 'DELETE' });
			this.message = `Deleted preset "${preset.name}".`;
		});
	}

	private update(
		preset: Preset,
		patch: { name?: string; values?: Preset['values'] },
		message: string | null
	): Promise<boolean> {
		return this.mutate(preset.id, async () => {
			await api<Preset>(`${this.base}/${preset.id}`, {
				method: 'PUT',
				headers: JSON_HEADERS,
				body: JSON.stringify({ ...patch, expected_revision: preset.revision })
			});
			this.message = message;
		});
	}

	private async mutate(id: string, action: () => Promise<void>): Promise<boolean> {
		this.busy = id;
		this.error = null;
		this.message = null;
		let ok = false;
		try {
			await action();
			ok = true;
		} catch (cause) {
			this.error =
				cause instanceof ApiRequestError && cause.status === 409
					? `${cause.detail.message} The list was refreshed.`
					: describeApiError(cause);
		}
		await this.load(); // the list is the truth after any outcome
		this.busy = null;
		return ok;
	}
}
