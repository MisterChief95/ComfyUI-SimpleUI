<script lang="ts">
	import Sheet from '$lib/ui/Sheet.svelte';
	import type { GalleryState } from './gallery.svelte';
	import { type ViewPrefs, MAX_TILE, MIN_TILE, type GallerySort } from './viewPrefs.svelte';
	let {
		open = $bindable(false),
		prefs,
		gallery,
		tile
	}: {
		open?: boolean;
		prefs: ViewPrefs;
		gallery: GalleryState;
		tile: number;
	} = $props();
</script>

<Sheet bind:open title="View settings" variant="sheet">
	<div class="field-grid">
		<label>
			<span>Sort</span>
			<select
				bind:value={
					() => prefs.sort,
					(sort: GallerySort) => {
						prefs.sort = sort;
						void gallery.load(true);
					}
				}
			>
				<option value="newest">Newest</option>
				<option value="oldest">Oldest</option>
				<option value="random">Random</option>
			</select>
		</label>
		<label>
			<span>Thumbnail width: {tile}px{prefs.size ? '' : ' (profile default)'}</span>
			<input
				type="range"
				min={MIN_TILE}
				max={MAX_TILE}
				step="8"
				bind:value={() => tile, (px: number) => (prefs.size = px)}
			/>
		</label>
		<label class="field-check"
			><input type="checkbox" bind:checked={prefs.fit} /> Show whole image (no crop)</label
		>
		<label class="field-check"
			><input type="checkbox" bind:checked={prefs.badges} /> Show video badges</label
		>
		<label class="field-check"
			><input
				type="checkbox"
				bind:checked={
					() => prefs.walk,
					(walk: boolean) => {
						prefs.walk = walk;
						void gallery.load(true);
					}
				}
			/> Walk across sibling folders</label
		>
	</div>
	{#snippet footer()}
		<button
			type="button"
			class="btn grow"
			onclick={() => {
				const changed = prefs.sort !== 'newest' || prefs.walk;
				prefs.reset();
				if (changed) void gallery.load(true);
			}}>Reset to defaults</button
		>
	{/snippet}
</Sheet>
