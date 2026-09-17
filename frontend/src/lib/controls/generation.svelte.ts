// Reactive state for one open workflow's generation form: schema, edits, seed
// policy, submission, and a live status subscription for the latest job.
import { api, describeApiError } from '$lib/api';
import type { ControlSchema, GenerationDetail, SeedPolicy } from '$lib/contracts';

export class GenerationFormState {
	readonly workflowId: string;
	schema = $state<ControlSchema | null>(null);
	loading = $state(true);
	loadError = $state<string | null>(null);
	edits = $state<Record<string, string>>({});
	seedPolicy = $state<SeedPolicy>('random');
	submitting = $state(false);
	submitError = $state<string | null>(null);
	latest = $state<GenerationDetail | null>(null);

	private socket: WebSocket | null = null;

	constructor(workflowId: string) {
		this.workflowId = workflowId;
	}

	async load(): Promise<void> {
		this.loading = true;
		this.loadError = null;
		try {
			this.schema = await api<ControlSchema>(`/workflows/${this.workflowId}/controls`);
			const reuseId = new URLSearchParams(location.search).get('reuse');
			if (reuseId) await this._reuse(reuseId);
		} catch (cause) {
			this.loadError = describeApiError(cause);
		} finally {
			this.loading = false;
		}
	}

	private async _reuse(generationId: string): Promise<void> {
		const generation = await api<GenerationDetail>(`/generations/${generationId}`);
		if (generation.workflow_id !== this.workflowId || !generation.effective_values) return;
		const fileBindings = new Set(
			this.schema?.controls
				.filter((control) => control.component === 'file')
				.map((control) => control.binding_id) ?? []
		);
		this.edits = {
			...Object.fromEntries([...fileBindings].map((bindingId) => [bindingId, ''])),
			...Object.fromEntries(
			Object.entries(generation.effective_values)
				.filter(([bindingId]) => !fileBindings.has(bindingId))
				.map(([bindingId, value]) => [bindingId, String(value)])
			)
		};
	}

	setEdit(bindingId: string, value: string): void {
		this.edits = { ...this.edits, [bindingId]: value };
	}

	async submit(): Promise<void> {
		this.submitting = true;
		this.submitError = null;
		try {
			// ponytail: a fresh key per click, not a remembered one, so a client
			// retry after a network-level failure (never reached the server) is
			// simplest as a new request. GenerationService still guarantees a
			// key that *did* reach the server is never resubmitted server-side.
			const requestKey =
				typeof crypto.randomUUID === 'function'
					? crypto.randomUUID()
					: `${Date.now()}-${Math.random()}`;
			this.latest = await api<GenerationDetail>('/generations', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					workflow_id: this.workflowId,
					request_key: requestKey,
					edits: this.edits,
					seed_policy: this.seedPolicy
				})
			});
			this._watch(this.latest.id);
		} catch (cause) {
			this.submitError = describeApiError(cause);
		} finally {
			this.submitting = false;
		}
	}

	/** Advisory push over /api/events; a lost frame just means a slower refresh. */
	private _watch(generationId: string): void {
		this.socket?.close();
		const url = new URL('/api/events', location.href);
		url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
		const socket = new WebSocket(url);
		socket.onmessage = (event) => {
			try {
				const data = JSON.parse(event.data);
				if (data.generation_id === generationId) this._refresh(generationId);
			} catch {
				// not JSON, or not this generation -- ignore
			}
		};
		this.socket = socket;
	}

	private async _refresh(generationId: string): Promise<void> {
		try {
			this.latest = await api<GenerationDetail>(`/generations/${generationId}`);
		} catch {
			// transient; the next event (or the user reopening this page) retries
		}
	}

	dispose(): void {
		this.socket?.close();
		this.socket = null;
	}
}
