import { api, describeApiError } from '$lib/api';
import { settingsState } from '$lib/settings.svelte';
import type { GenerationDetail, MediaInfo, MediaPage } from '$lib/contracts';
import type { ViewPrefs } from './viewPrefs.svelte';
import { advance, isLeaf, sibling, type WalkCursor, type WalkFolder } from './walk';

type Folder = { path: string; name: string; count: number };
type Collection = { id: string; name: string; count: number };
type MediaTree = {
	path: string;
	timezone: string;
	count: number;
	children: Folder[];
	breadcrumbs: { path: string; name: string }[];
};

export class GalleryState {
	downloadBusy = $state(false);
	downloadMessage = $state('');
	async downloadZip(ids: string[], csrfToken: string | null): Promise<void> {
		if (this.downloadBusy) return;
		this.downloadBusy = true;
		this.downloadMessage = '';
		this.error = null;
		try {
			const selection = JSON.stringify({ ids });
			const plan = await api<{ entries: object[]; skipped: object[] }>(
				'/media/download-zip/preview',
				{
					method: 'POST',
					headers: { 'content-type': 'application/json' },
					body: selection
				}
			);
			if (!plan.entries.length) {
				this.downloadMessage = 'No selected files are available. Nothing was downloaded.';
				return;
			}
			this.downloadMessage = `${plan.entries.length} files requested for download. ${plan.skipped.length ? `${plan.skipped.length} unavailable items will be skipped. ` : ''}Check manifest.json in the ZIP for the final included and skipped items; files can become unavailable during download.`;
			// Let the browser download to disk; never buffer the archive in a JS blob.
			const form = document.createElement('form');
			form.method = 'POST';
			form.action = '/api/media/download-zip';
			for (const [name, value] of Object.entries({ selection, csrf_token: csrfToken ?? '' })) {
				const input = document.createElement('input');
				input.type = 'hidden';
				input.name = name;
				input.value = value;
				form.append(input);
			}
			document.body.append(form);
			try {
				form.submit();
			} finally {
				form.remove();
			}
		} catch (cause) {
			this.error = describeApiError(cause);
		} finally {
			this.downloadBusy = false;
		}
	}

	constructor(private viewPrefs?: ViewPrefs) {}
	private pendingReset = false;
	folderPath = $state('');
	tree = $state.raw<MediaTree | null>(null);
	collections = $state.raw<Collection[]>([]);
	collectionId = $state('');
	collectionBusy = $state(false);
	get currentCollection(): string {
		return this.folderPath.startsWith('Collections/')
			? this.folderPath.slice(12)
			: this.applied.collectionId;
	}

	async changeCollection(
		method: string,
		id: string,
		body?: object
	): Promise<{ id: string } | null> {
		if (this.collectionBusy) return null;
		this.collectionBusy = true;
		this.error = null;
		try {
			const result = await api<{ id: string }>(`/media/collections${id ? `/${id}` : ''}`, {
				method,
				headers: { 'content-type': 'application/json' },
				body: body ? JSON.stringify(body) : undefined
			});
			if (method === 'DELETE') {
				if (this.collectionId === id) this.collectionId = '';
				if (this.applied.collectionId === id) this.applied.collectionId = '';
				if (this.folderPath === `Collections/${id}`) this.folderPath = 'Collections';
			}
			await this.load(true);
			return result;
		} catch (cause) {
			this.error = describeApiError(cause);
			return null;
		} finally {
			this.collectionBusy = false;
		}
	}

	navigate(path: string): Promise<void> {
		this.folderPath = path;
		this.tree = null;
		this.items = [];
		this.continuation = null;
		this.close();
		return this.load(true);
	}
	items = $state.raw<MediaInfo[]>([]);
	// Group and opaque backend position travel together; positions never cross leaves.
	continuation = $state.raw<WalkCursor | null>(null);
	private siblings = $state.raw<WalkFolder[]>([]);
	private firstGroup = $state('');
	private itemGroups = $state<Record<string, string>>({});
	get canLoadMore(): boolean {
		return this.continuation !== null;
	}
	get canLoadPrevious(): boolean {
		return sibling(this.siblings, this.firstGroup, -1) !== null;
	}

	groupHeader(index: number): string | null {
		if (!this.viewPrefs?.walk || !this.siblings.length) return null;
		const group = this.itemGroups[this.items[index]?.id];
		if (!group || (index > 0 && this.itemGroups[this.items[index - 1].id] === group)) return null;
		return group.startsWith('Date/')
			? group
			: (this.siblings.find((folder) => folder.path === group)?.name ?? group);
	}
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
	searchField = $state('any');
	suggestions = $state.raw<string[]>([]);
	private suggestionTimer: ReturnType<typeof setTimeout> | undefined;
	private suggestionRequest: AbortController | null = null;

	setPrompt(value: string): void {
		const selected = this.suggestions.includes(value);
		this.prompt = value;
		this.clearSuggestions();
		if (selected) {
			void this.apply();
			return;
		}
		const q = value.trim();
		if (q.length < 2) return;
		this.suggestionTimer = setTimeout(async () => {
			const request = new AbortController();
			this.suggestionRequest = request;
			try {
				const result = await api<{ items: string[] }>(
					`/media/suggestions?${new URLSearchParams({ q, search_field: this.searchField })}`,
					{ signal: request.signal }
				);
				if (!request.signal.aborted && this.prompt.trim() === q) this.suggestions = result.items;
			} catch {
				// Suggestions are optional; manual filtering still works while offline.
			} finally {
				if (this.suggestionRequest === request) this.suggestionRequest = null;
			}
		}, 250);
	}

	setSearchField(value: string): void {
		this.searchField = value;
		this.clearSuggestions();
	}

	private clearSuggestions(): void {
		clearTimeout(this.suggestionTimer);
		this.suggestionRequest?.abort();
		this.suggestionRequest = null;
		this.suggestions = [];
	}

	private socket: WebSocket | null = null;
	private applied = $state({
		mediaKind: '',
		favorite: '',
		workflowId: '',
		collectionId: '',
		createdAfter: '',
		createdBefore: '',
		prompt: '',
		searchField: 'any'
	});

	/** Number of filters currently in effect (for the Filters button badge). */
	get activeCount(): number {
		return Object.entries(this.applied).filter(
			([key, value]) => key !== 'searchField' && value.trim() !== ''
		).length;
	}

	/** Position of the open item in the loaded list, or -1 (e.g. after a refresh dropped it). */
	get selectedIndex(): number {
		const id = this.selected?.id;
		return id ? this.items.findIndex((entry) => entry.id === id) : -1;
	}

	/** Clear every filter and reload. */
	resetFilters(): Promise<void> {
		this.mediaKind = '';
		this.favorite = '';
		this.workflowId = '';
		this.collectionId = '';
		this.createdAfter = '';
		this.createdBefore = '';
		this.prompt = '';
		this.searchField = 'any';
		return this.apply();
	}

	/** Open the previous (-1) or next (+1) loaded item; loads more at the end of the list. */
	async step(delta: -1 | 1): Promise<void> {
		if (this.loading) return;
		const index = this.selectedIndex;
		const selectedId = this.selected?.id;
		if (index < 0) return;
		const target = this.items[index + delta];
		if (target) {
			await this.select(target);
		} else if (delta === 1 && this.canLoadMore) {
			await this.load(false);
			if (this.selected?.id !== selectedId || this.selectedIndex < 0) return;
			const next = this.items[this.selectedIndex + 1];
			if (next) await this.select(next);
		} else if (delta === -1 && this.canLoadPrevious) {
			await this.loadPrevious();
			if (this.selected?.id !== selectedId || this.selectedIndex < 0) return;
			const previous = this.items[this.selectedIndex - 1];
			if (previous) await this.select(previous);
		}
	}

	apply(): Promise<void> {
		this.clearSuggestions();
		this.applied = {
			mediaKind: this.mediaKind,
			favorite: this.favorite,
			workflowId: this.workflowId,
			collectionId: this.collectionId,
			createdAfter: this.createdAfter,
			createdBefore: this.createdBefore,
			prompt: this.prompt,
			searchField: this.searchField
		};
		return this.load(true);
	}

	async load(reset = true): Promise<void> {
		if (this.loading) {
			this.pendingReset ||= reset;
			return;
		}
		if (!reset && !this.continuation) return;
		this.loading = true;
		this.error = null;
		try {
			if (reset) {
				this.continuation = null;
				this.siblings = [];
				const path = this.folderPath;
				const [tree, parent, collections] = await Promise.all([
					this.fetchTree(path),
					this.viewPrefs?.walk && isLeaf(path)
						? this.fetchTree(path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
						: Promise.resolve(null),
					api<{ items: Collection[] }>('/media/collections')
				]);
				if (this.pendingReset) return;
				this.tree = tree;
				this.collections = collections.items;
				this.siblings = parent?.children ?? [];
				this.firstGroup = path;
				this.items = [];
				this.itemGroups = {};
				this.continuation = { group: path, position: null };
			}
			// Filters can empty entire leaves. Keep advancing until a visible page or the end.
			while (this.continuation) {
				const cursor = this.continuation;
				const page = await api<MediaPage>(`/media?${this._query(cursor.position, cursor.group)}`);
				if (this.pendingReset) return;
				const seen = new Set(this.items.map((item) => item.id));
				const added = page.items.filter((item) => !seen.has(item.id));
				for (const item of added) this.itemGroups[item.id] = cursor.group;
				this.items = [...this.items, ...added];
				this.continuation = advance(this.siblings, cursor.group, page.next_cursor);
				if (added.length) break;
			}
		} catch (cause) {
			this.error = describeApiError(cause);
		} finally {
			this.loading = false;
			if (this.pendingReset) {
				this.pendingReset = false;
				await this.load(true);
			}
		}
	}

	private fetchTree(path: string): Promise<MediaTree> {
		return api<MediaTree>(`/media/tree?${new URLSearchParams({ path })}`);
	}

	private async loadPrevious(): Promise<void> {
		this.loading = true;
		this.error = null;
		try {
			let previous = sibling(this.siblings, this.firstGroup, -1);
			while (previous) {
				const group = previous.path;
				const items: MediaInfo[] = [];
				let position: string | null = null;
				// To step backward in any sort, including random, finish this leaf's pages first.
				// ponytail: loads one whole previous leaf; add reverse keyset paging if large leaves need it.
				do {
					const page: MediaPage = await api<MediaPage>(`/media?${this._query(position, group)}`);
					if (this.pendingReset) return;
					items.push(...page.items);
					position = page.next_cursor;
				} while (position);
				const seen = new Set(this.items.map((item) => item.id));
				const added = items.filter((item) => !seen.has(item.id));
				for (const item of added) this.itemGroups[item.id] = group;
				this.items = [...added, ...this.items];
				this.firstGroup = group;
				if (added.length) break;
				previous = sibling(this.siblings, group, -1);
			}
		} catch (cause) {
			this.error = describeApiError(cause);
		} finally {
			this.loading = false;
			if (this.pendingReset) {
				this.pendingReset = false;
				await this.load(true);
			}
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
		this.detailLoading = false;
		if (!item.generation_id) return;
		this.detailLoading = true;
		try {
			const detail = await api<GenerationDetail>(`/generations/${item.generation_id}`);
			if (this.selected?.id === item.id) this.detail = detail;
		} catch (cause) {
			if (this.selected?.id === item.id) this.detailError = describeApiError(cause);
		} finally {
			if (this.selected?.id === item.id) this.detailLoading = false;
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
			await this.load(true);
		} catch (cause) {
			this.error = describeApiError(cause);
		}
	}

	/** Delete on the server, then drop the items locally. Returns whether it succeeded. */
	async deleteMany(ids: string[]): Promise<boolean> {
		try {
			await api('/media/delete', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ ids })
			});
		} catch (cause) {
			this.error = describeApiError(cause);
			return false;
		}
		const gone = new Set(ids);
		this.items = this.items.filter((entry) => !gone.has(entry.id));
		if (this.selected && gone.has(this.selected.id)) this.close();
		await this.load(true);
		return true;
	}

	dispose(): void {
		this.clearSuggestions();
		this.socket?.close();
		this.socket = null;
	}

	private _query(cursor: string | null, group: string): URLSearchParams {
		const pageSize = Number(settingsState.data?.profile.gallery_page_size ?? 50);
		const query = new URLSearchParams({ limit: String(Math.max(1, Math.min(200, pageSize))) });
		query.set('sort', this.viewPrefs?.sort ?? 'newest');
		if (group) query.set('path', group);
		if (cursor) query.set('cursor', cursor);
		if (this.applied.mediaKind) query.set('media_kind', this.applied.mediaKind);
		if (this.applied.favorite) query.set('favorite', this.applied.favorite);
		if (this.applied.workflowId.trim()) query.set('workflow_id', this.applied.workflowId.trim());
		if (this.applied.collectionId) query.set('collection_id', this.applied.collectionId);
		if (this.applied.prompt.trim()) {
			query.set('prompt', this.applied.prompt.trim());
			query.set('search_field', this.applied.searchField);
		}
		if (this.applied.createdAfter)
			query.set('created_after', String(this._start(this.applied.createdAfter)));
		if (this.applied.createdBefore)
			query.set('created_before', String(this._end(this.applied.createdBefore)));
		return query;
	}

	private _start(value: string): number {
		return new Date(`${value}T00:00:00`).getTime();
	}

	private _end(value: string): number {
		return new Date(`${value}T23:59:59.999`).getTime();
	}
}
