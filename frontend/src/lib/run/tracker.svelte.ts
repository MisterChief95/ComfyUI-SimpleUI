// Follows one workflow's latest generation: /api/events (reconnecting) for
// live progress, GET /api/generations/{id} on any event plus a 3s poll as the
// fallback, its output media, and the workflow's recent-outputs strip.
// Event delivery is advisory (docs/ARCHITECTURE.md): the GET is the truth.
import { api } from '$lib/api';
import { settingsState } from '$lib/settings.svelte';
import type { GenerationDetail, GenerationInfo, MediaInfo, Page } from '$lib/contracts';
import { nodeProgress, parseFrame, previewUrl, type NodeProgress } from './events';
import { isTerminal } from './status';

const POLL_MS = 3000;
const THROTTLE_MS = 400;
const MAX_BACKOFF_MS = 10_000;
const OUTPUT_STATES = new Set(['ready', 'partial']);
// Survives route changes, but resets when the app reloads.
const sessionGenerations = new Set<string>();
const clearedWorkflows = new Map<string, string | null>();

export class GenerationTracker {
	readonly workflowId: string;
	latest = $state<GenerationDetail | null>(null);
	/** Sampler progress from `progress` events; cleared when the job ends. */
	progress = $state<{ value: number; max: number } | null>(null);
	/** Node id from the last `executing` event. */
	nodeId = $state<string | null>(null);
	/** Finished/total nodes from the last `progress_state` event. */
	nodes = $state<NodeProgress | null>(null);
	/** Latest `preview` frame as a data URL (only sent when ComfyUI makes previews). */
	preview = $state<string | null>(null);
	/** Media captured by the latest generation. */
	outputs = $state<MediaInfo[]>([]);
	recent = $state<MediaInfo[]>([]);
	/** A strip item the user chose to look at instead of the latest result. */
	picked = $state<MediaInfo | null>(null);
	/** Complete output group for a generation chosen from the recent strip. */
	pickedOutputs = $state<MediaInfo[]>([]);
	connected = $state(false);

	shown = $derived(this.picked ?? this.outputs[0] ?? this.recent[0] ?? null);
	active = $derived(this.latest !== null && !isTerminal(this.latest.status));

	private socket: WebSocket | null = null;
	private stopped = true;
	private attempt = 0;
	private reconnectTimer: ReturnType<typeof setTimeout> | undefined;
	private pollTimer: ReturnType<typeof setInterval> | undefined;
	private throttleTimer: ReturnType<typeof setTimeout> | undefined;
	private inflight = false;
	private again = false;
	private outputsKey = '';
	private pendingPolls = 0;
	private resultsRevision = 0;
	private currentRunOnly = false;

	constructor(workflowId: string) {
		this.workflowId = workflowId;
		this.currentRunOnly = clearedWorkflows.has(workflowId);
	}

	/** Open the socket + poll timer, then pick up this workflow's newest generation. */
	async start(): Promise<void> {
		const revision = this.resultsRevision;
		this.stopped = false;
		this.connect();
		this.pollTimer = setInterval(() => {
			if (this.latest && (this.active || this.outputPending())) void this.refresh();
		}, POLL_MS);
		void this.refreshRecent();
		try {
			const page = await api<Page<GenerationInfo>>('/generations?limit=20');
			const mine = page.items.find((item) => item.workflow_id === this.workflowId);
			if (
				mine &&
				!this.latest &&
				revision === this.resultsRevision &&
				!this.stopped &&
				(!this.currentRunOnly || clearedWorkflows.get(this.workflowId) === mine.id) &&
				(!settingsState.data?.profile.clear_generation_on_startup ||
					!isTerminal(mine.status) ||
					sessionGenerations.has(mine.id))
			)
				await this.adopt(mine.id);
		} catch {
			// no history yet is fine; the strip below still shows older outputs
		}
	}

	stop(): void {
		this.stopped = true;
		this.resultsRevision += 1;
		this.socket?.close();
		this.socket = null;
		clearTimeout(this.reconnectTimer);
		clearTimeout(this.throttleTimer);
		clearInterval(this.pollTimer);
	}

	/** Start following a generation (just submitted, or the last one on page load). */
	async adopt(generationOrId: GenerationDetail | string): Promise<void> {
		const revision = ++this.resultsRevision;
		this.clearLive();
		this.outputs = [];
		this.outputsKey = '';
		this.picked = null;
		this.pendingPolls = 0;
		if (typeof generationOrId === 'string') {
			try {
				const detail = await api<GenerationDetail>(`/generations/${generationOrId}`);
				if (revision !== this.resultsRevision) return;
				this.latest = detail;
			} catch {
				return;
			}
		} else {
			this.latest = generationOrId;
		}
		sessionGenerations.add(this.latest.id);
		if (this.currentRunOnly) clearedWorkflows.set(this.workflowId, this.latest.id);
		await this.syncOutputs();
	}

	/** Hide results locally; saved media and generation records are untouched. */
	clearResults(): void {
		this.resultsRevision += 1;
		this.currentRunOnly = true;
		clearedWorkflows.set(this.workflowId, null);
		this.latest = null;
		this.outputs = [];
		this.recent = [];
		this.picked = null;
		this.pickedOutputs = [];
		this.outputsKey = '';
		this.clearLive();
	}

	/** Re-GET the latest generation; calls coalesce so a burst of events costs one request. */
	async refresh(): Promise<void> {
		if (this.inflight) {
			this.again = true;
			return;
		}
		this.inflight = true;
		try {
			await this.fetchLatest();
		} finally {
			this.inflight = false;
			if (this.again) {
				this.again = false;
				void this.refresh();
			}
		}
	}

	async refreshRecent(): Promise<void> {
		const revision = this.resultsRevision;
		try {
			const page = await api<Page<MediaInfo>>(`/media?workflow_id=${this.workflowId}&limit=12`);
			if (revision !== this.resultsRevision) return;
			this.recent = page.items.filter((item) =>
				this.currentRunOnly
					? item.generation_id === this.latest?.id
					: !settingsState.data?.profile.clear_generation_on_startup ||
						(item.generation_id !== null && sessionGenerations.has(item.generation_id))
			);
		} catch {
			// advisory strip only
		}
	}

	async pick(item: MediaInfo): Promise<void> {
		const previous = this.picked?.generation_id;
		this.picked = item;
		if (item.generation_id === this.latest?.id || item.generation_id === previous) return;
		this.pickedOutputs = [item];
		if (!item.generation_id) return;
		try {
			const outputs = await this.loadOutputs(item.generation_id);
			if (this.picked?.generation_id === item.generation_id) this.pickedOutputs = outputs;
		} catch {
			// The chosen output remains usable if the rest cannot be loaded.
		}
	}

	private async loadOutputs(id: string): Promise<MediaInfo[]> {
		const outputs: MediaInfo[] = [];
		let cursor: string | null = null;
		do {
			const page: Page<MediaInfo> = await api(
				`/media?generation_id=${id}&limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`
			);
			outputs.push(...page.items);
			cursor = page.next_cursor;
		} while (cursor);
		return outputs;
	}

	private outputPending(): boolean {
		// A finished job whose outputs are still being captured: poll briefly, not forever.
		if (this.latest?.output_state === 'pending' && this.pendingPolls < 20) {
			this.pendingPolls += 1;
			return true;
		}
		return false;
	}

	private async fetchLatest(): Promise<void> {
		const current = this.latest;
		if (!current) return;
		try {
			const next = await api<GenerationDetail>(`/generations/${current.id}`);
			if (this.latest?.id !== current.id) return; // user submitted another meanwhile
			this.latest = next;
			if (isTerminal(next.status)) this.clearLive();
			await this.syncOutputs();
		} catch {
			// transient; the next event or poll retries
		}
	}

	private clearLive(): void {
		this.progress = null;
		this.nodeId = null;
		this.nodes = null;
		this.preview = null;
	}

	private async syncOutputs(): Promise<void> {
		const generation = this.latest;
		if (!generation) return;
		const key = `${generation.id}:${generation.output_state}`;
		if (key === this.outputsKey) return;
		this.outputsKey = key;
		if (!OUTPUT_STATES.has(generation.output_state)) {
			this.outputs = [];
			return;
		}
		try {
			const outputs = await this.loadOutputs(generation.id);
			if (this.latest?.id === generation.id) this.outputs = outputs;
		} catch {
			this.outputsKey = ''; // retry on the next refresh
			return;
		}
		void this.refreshRecent();
	}

	// --- events -----------------------------------------------------------

	private connect(): void {
		if (this.stopped) return;
		const url = new URL('/api/events', location.href);
		url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
		const socket = new WebSocket(url);
		this.socket = socket;
		socket.onopen = () => {
			this.attempt = 0;
			this.connected = true;
			// Frames sent while we were away are gone; catch up from the snapshot.
			if (this.latest) void this.refresh();
		};
		socket.onmessage = (event) => this.onEvent(event.data);
		socket.onclose = () => {
			if (this.socket === socket) this.socket = null;
			this.connected = false;
			if (this.stopped) return;
			const delay = Math.min(MAX_BACKOFF_MS, 500 * 2 ** this.attempt);
			this.attempt += 1;
			this.reconnectTimer = setTimeout(() => this.connect(), delay);
		};
	}

	/** Public for tests/tools: feed one raw /api/events frame. */
	onEvent(raw: unknown): void {
		const frame = parseFrame(raw);
		if (!frame || !this.latest || frame.generation_id !== this.latest.id) return;
		const { data } = frame;
		if (frame.type === 'preview') {
			this.preview = previewUrl(data) ?? this.preview;
			return; // ~2/s; nothing to re-fetch
		}
		if (frame.type === 'progress_state') {
			this.nodes = nodeProgress(data) ?? this.nodes;
			return;
		}
		if (frame.type === 'progress') {
			const value = Number(data.value);
			const max = Number(data.max);
			if (Number.isFinite(value) && Number.isFinite(max) && max > 0) this.progress = { value, max };
		} else if (frame.type === 'executing') {
			this.nodeId = typeof data.node === 'string' ? data.node : null;
			this.progress = null;
		}
		if (this.throttleTimer === undefined) {
			this.throttleTimer = setTimeout(() => {
				this.throttleTimer = undefined;
				void this.refresh();
			}, THROTTLE_MS);
		}
	}
}
