import { api, describeApiError } from '$lib/api';
import type {
	ClearHistoryResult,
	GenerationDetail,
	GenerationInfo,
	Page
} from '$lib/contracts';

export class HistoryState {
	items = $state.raw<GenerationInfo[]>([]);
	nextCursor = $state<string | null>(null);
	loading = $state(false);
	error = $state<string | null>(null);
	selected = $state.raw<GenerationDetail | null>(null);
	selectedError = $state<string | null>(null);
	clearing = $state(false);
	clearResult = $state<ClearHistoryResult | null>(null);

	private socket: WebSocket | null = null;

	async load(reset = true): Promise<void> {
		if (this.loading) return;
		this.loading = true;
		this.error = null;
		try {
			const cursor = reset || !this.nextCursor ? '' : `&cursor=${encodeURIComponent(this.nextCursor)}`;
			const page = await api<Page<GenerationInfo>>(`/generations?limit=50${cursor}`);
			this.items = reset ? page.items : [...this.items, ...page.items];
			this.nextCursor = page.next_cursor;
		} catch (cause) {
			this.error = describeApiError(cause);
		} finally {
			this.loading = false;
		}
	}

	connect(): void {
		const url = new URL('/api/events', location.href);
		url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
		this.socket = new WebSocket(url);
		this.socket.onmessage = () => this.load(true);
	}

	async select(id: string): Promise<void> {
		this.selectedError = null;
		try {
			this.selected = await api<GenerationDetail>(`/generations/${id}`);
		} catch (cause) {
			this.selectedError = describeApiError(cause);
		}
	}

	async clear(): Promise<void> {
		if (!confirm('Clear reusable prompt and workflow snapshots from completed generations? Media files and generation status records will remain.')) return;
		this.clearing = true;
		this.error = null;
		try {
			this.clearResult = await api<ClearHistoryResult>('/media/clear-history', { method: 'POST' });
			if (this.selected) await this.select(this.selected.id);
			await this.load(true);
		} catch (cause) {
			this.error = describeApiError(cause);
		} finally {
			this.clearing = false;
		}
	}

	dispose(): void {
		this.socket?.close();
		this.socket = null;
	}
}
