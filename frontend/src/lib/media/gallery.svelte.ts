import { api, describeApiError } from '$lib/api';
import type { GenerationDetail, MediaInfo, MediaPage } from '$lib/contracts';

export class GalleryState {
	items = $state.raw<MediaInfo[]>([]);
	nextCursor = $state<string | null>(null);
	loading = $state(false);
	error = $state<string | null>(null);
	selected = $state<MediaInfo | null>(null);
	detail = $state.raw<GenerationDetail | null>(null);
	detailLoading = $state(false);
	detailError = $state<string | null>(null);
	unavailable = $state<Record<string, boolean>>({});
	thumbnailMissing = $state<Record<string, boolean>>({});

	mediaKind = $state('');
	favorite = $state('');
	workflowId = $state('');
	createdAfter = $state('');
	createdBefore = $state('');
	prompt = $state('');

	private socket: WebSocket | null = null;
	private applied = {
		mediaKind: '', favorite: '', workflowId: '', createdAfter: '', createdBefore: '', prompt: ''
	};

	apply(): Promise<void> {
		this.applied = {
			mediaKind: this.mediaKind,
			favorite: this.favorite,
			workflowId: this.workflowId,
			createdAfter: this.createdAfter,
			createdBefore: this.createdBefore,
			prompt: this.prompt
		};
		return this.load(true);
	}

	async load(reset = true): Promise<void> {
		if (this.loading) return;
		this.loading = true;
		this.error = null;
		try {
			const query = this._query(reset ? null : this.nextCursor);
			const page = await api<MediaPage>(`/media?${query}`);
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

	async select(item: MediaInfo): Promise<void> {
		this.selected = item;
		this.detail = null;
		this.detailError = null;
		if (!item.generation_id) return;
		this.detailLoading = true;
		try {
			this.detail = await api<GenerationDetail>(`/generations/${item.generation_id}`);
		} catch (cause) {
			this.detailError = describeApiError(cause);
		} finally {
			this.detailLoading = false;
		}
	}

	close(): void {
		this.selected = null;
		this.detail = null;
	}

	markUnavailable(id: string): void {
		this.unavailable = { ...this.unavailable, [id]: true };
	}

	markThumbnailMissing(id: string): void {
		this.thumbnailMissing = { ...this.thumbnailMissing, [id]: true };
	}

	isUnavailable(item: MediaInfo): boolean {
		return item.state === 'unavailable' || this.unavailable[item.id] === true;
	}

	async toggleFavorite(item: MediaInfo): Promise<void> {
		const favorite = !item.favorite;
		try {
			await api(`/media/${item.id}`, {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ favorite })
			});
			this.items = this.items.map((entry) =>
				entry.id === item.id ? { ...entry, favorite } : entry
			);
			if (this.selected?.id === item.id) this.selected = { ...this.selected, favorite };
		} catch (cause) {
			this.error = describeApiError(cause);
		}
	}

	dispose(): void {
		this.socket?.close();
		this.socket = null;
	}

	private _query(cursor: string | null): URLSearchParams {
		const query = new URLSearchParams({ limit: '48' });
		if (cursor) query.set('cursor', cursor);
		if (this.applied.mediaKind) query.set('media_kind', this.applied.mediaKind);
		if (this.applied.favorite) query.set('favorite', this.applied.favorite);
		if (this.applied.workflowId.trim()) query.set('workflow_id', this.applied.workflowId.trim());
		if (this.applied.prompt.trim()) query.set('prompt', this.applied.prompt.trim());
		if (this.applied.createdAfter) query.set('created_after', String(this._start(this.applied.createdAfter)));
		if (this.applied.createdBefore) query.set('created_before', String(this._end(this.applied.createdBefore)));
		return query;
	}

	private _start(value: string): number {
		return new Date(`${value}T00:00:00`).getTime();
	}

	private _end(value: string): number {
		return new Date(`${value}T23:59:59.999`).getTime();
	}
}
