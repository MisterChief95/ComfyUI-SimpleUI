import { readJson, writeJson } from '$lib/ui/storage';
// Per-device gallery view preferences. localStorage is a convenience only: every access is
// guarded, and a `size` of 0 means "use the profile's thumbnail_size setting".
const KEY = 'gallery.viewPrefs';

// Minimum tile width in px for the gallery grid slider.
export const MIN_TILE = 80;
export const MAX_TILE = 400;
export type GallerySort = 'newest' | 'oldest' | 'random';

export class ViewPrefs {
	sort = $state<GallerySort>('newest');
	size = $state(0);
	fit = $state(false);
	badges = $state(true);
	walk = $state(false);

	constructor() {
		try {
			const saved = readJson<Partial<ViewPrefs>>(KEY, {});
			if (saved.sort && ['newest', 'oldest', 'random'].includes(saved.sort)) this.sort = saved.sort;
			if (typeof saved.size === 'number' && saved.size >= MIN_TILE && saved.size <= MAX_TILE)
				this.size = saved.size;
			if (typeof saved.fit === 'boolean') this.fit = saved.fit;
			if (typeof saved.badges === 'boolean') this.badges = saved.badges;
			if (typeof saved.walk === 'boolean') this.walk = saved.walk;
		} catch {
			/* storage blocked or corrupt: defaults */
		}
	}

	save(): void {
		writeJson(KEY, {
			sort: this.sort,
			size: this.size,
			fit: this.fit,
			badges: this.badges,
			walk: this.walk
		});
	}

	reset(): void {
		this.sort = 'newest';
		this.size = 0;
		this.fit = false;
		this.badges = true;
		this.walk = false;
		this.save();
	}
}
