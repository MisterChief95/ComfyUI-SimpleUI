// Per-device gallery view preferences. localStorage is a convenience only: every access is
// guarded, and an empty `size` means "use the profile's thumbnail_size setting".
const KEY = 'gallery.viewPrefs';

export type TileSize = '' | 'small' | 'medium' | 'large';
export type GallerySort = 'newest' | 'oldest' | 'random';

export class ViewPrefs {
	sort = $state<GallerySort>('newest');
	size = $state<TileSize>('');
	fit = $state(false);
	badges = $state(true);
	walk = $state(false);

	constructor() {
		try {
			const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}');
			if (['newest', 'oldest', 'random'].includes(saved.sort)) this.sort = saved.sort;
			if (['', 'small', 'medium', 'large'].includes(saved.size)) this.size = saved.size;
			if (typeof saved.fit === 'boolean') this.fit = saved.fit;
			if (typeof saved.badges === 'boolean') this.badges = saved.badges;
			if (typeof saved.walk === 'boolean') this.walk = saved.walk;
		} catch {
			/* storage blocked or corrupt: defaults */
		}
	}

	save(): void {
		try {
			localStorage.setItem(KEY, JSON.stringify({ sort: this.sort, size: this.size, fit: this.fit, badges: this.badges, walk: this.walk }));
		} catch {
			/* not persisted */
		}
	}

	reset(): void {
		this.sort = 'newest';
		this.size = '';
		this.fit = false;
		this.badges = true;
		this.walk = false;
		this.save();
	}
}
