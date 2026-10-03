<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { MediaQuery } from 'svelte/reactivity';
	import { flip } from 'svelte/animate';
	import { fade } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { GalleryState } from '$lib/media/gallery.svelte';
	import { openViewer } from '$lib/media/openViewer';
	import { workflowNames } from '$lib/media/workflowNames.svelte';
	import { settingsState } from '$lib/settings.svelte';
	import { session } from '$lib/session.svelte';
	import Compare from '$lib/media/Compare.svelte';
	import type { MediaInfo } from '$lib/contracts';
	import Viewer from '$lib/media/Viewer.svelte';
	import { ViewPrefs, MAX_TILE, MIN_TILE, type GallerySort } from '$lib/media/viewPrefs.svelte';
	import Star from '$lib/media/Star.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';

	const prefs = new ViewPrefs();
	const gallery = new GalleryState(prefs);
	const wide = new MediaQuery('min-width: 768px');
	type GridEntry = { key: string; group: string | null; item: MediaInfo | null };
	const gridEntries = $derived(
		gallery.items.flatMap<GridEntry>((item, index) => {
			const group = gallery.groupHeader(index);
			const tile = { key: item.id, group: null, item };
			return group ? [{ key: `group:${item.id}`, group, item: null }, tile] : [tile];
		})
	);

	$effect(() => {
		void [prefs.sort, prefs.size, prefs.fit, prefs.badges, prefs.walk];
		prefs.save();
	});
	let prefsOpen = $state(false);
	let panelOpen = $state(false);
	let sheetOpen = $state(false);
	let comparing = $state(false);
	let picked = $state<MediaInfo[]>([]);
	let showCompare = $state(false);

	let selecting = $state(false);
	let chosen = $state<string[]>([]);
	let collectionOpen = $state(false);
	let collectionChoice = $state('');
	let collectionName = $state('');
	let renameName = $state('');

	async function addToCollection(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		let id = collectionChoice;
		if (!id) {
			const created = await gallery.changeCollection('POST', '', { name: collectionName });
			if (!created) return;
			id = created.id;
			collectionChoice = id;
		}
		if (await gallery.changeCollection('POST', `${id}/media`, { ids: chosen })) {
			collectionOpen = false;
			chosen = [];
		}
	}

	async function removeFromCollection(): Promise<void> {
		if (
			await gallery.changeCollection('POST', `${gallery.currentCollection}/remove`, { ids: chosen })
		)
			chosen = [];
	}

	// Last tile clicked in select mode; Shift+click selects everything between it and the new tile.
	let anchor = '';
	function open(item: MediaInfo, event: MouseEvent): void {
		const range = event.shiftKey;
		if (!selecting && (range || event.ctrlKey || event.metaKey)) toggleSelect();
		if (selecting) {
			const from = gallery.items.findIndex((i) => i.id === anchor);
			const to = gallery.items.indexOf(item);
			if (range && chosen.length && from >= 0) {
				const ids = gallery.items
					.slice(Math.min(from, to), Math.max(from, to) + 1)
					.map((i) => i.id);
				chosen = [...new Set([...chosen, ...ids])];
			} else {
				chosen = chosen.includes(item.id)
					? chosen.filter((id) => id !== item.id)
					: [...chosen, item.id];
			}
			anchor = item.id;
			return;
		}
		if (!comparing) {
			const source =
				item.media_kind === 'image'
					? (event.currentTarget as HTMLButtonElement).querySelector('img')
					: null;
			void openViewer(source, () => void gallery.select(item));
			return;
		}
		picked = picked.some((p) => p.id === item.id)
			? picked.filter((p) => p.id !== item.id)
			: [...picked, item].slice(-2);
	}
	function toggleCompare(): void {
		comparing = !comparing;
		picked = [];
		selecting = false;
	}
	function toggleSelect(): void {
		selecting = !selecting;
		chosen = [];
		anchor = '';
		comparing = false;
		picked = [];
	}
	async function deleteChosen(): Promise<void> {
		const n = chosen.length;
		if (
			!n ||
			!confirm(
				`Delete ${n} item${n === 1 ? '' : 's'}? This also removes ${n === 1 ? 'it' : 'them'} from the generation page and cannot be undone.`
			)
		)
			return;
		if (await gallery.deleteMany(chosen)) toggleSelect();
	}

	// This device's slider wins over the profile setting; either only changes the grid's minimum column width.
	const profileTile = $derived(
		{ small: 120, large: 208 }[String(settingsState.data?.profile.thumbnail_size)] ?? 160
	);
	const tile = $derived(prefs.size || profileTile);

	// Date folders arrive as bare digits (2026 / 10 / 03); show month and day names instead. Dates are UTC.
	function folderName(path: string, name: string): string {
		const m = /^Date\/(\d{4})\/(\d{2})(?:\/(\d{2}))?$/.exec(path);
		if (!m) return name;
		const date = new Date(Date.UTC(+m[1], +m[2] - 1, +(m[3] ?? 1)));
		return date.toLocaleDateString(undefined, {
			timeZone: 'UTC',
			...(m[3] ? { weekday: 'short', month: 'short', day: 'numeric' } : { month: 'long' })
		});
	}

	onMount(() => {
		let disposed = false;
		async function load(): Promise<void> {
			if (!settingsState.data) await settingsState.load();
			if (disposed) return;
			await gallery.apply();
			if (!disposed) gallery.connect();
		}
		void load();
		workflowNames.load();
		return () => {
			disposed = true;
		};
	});
	onDestroy(() => gallery.dispose());

	// Infinite scroll: a fresh observer after every page/loading change re-fires if the sentinel is still in range.
	let sentinel = $state<HTMLElement>();
	$effect(() => {
		void gallery.items.length;
		void gallery.loading;
		if (!sentinel) return;
		const io = new IntersectionObserver(
			([entry]) => {
				if (entry.isIntersecting && !gallery.loading && !gallery.error && gallery.canLoadMore)
					void gallery.load(false);
			},
			{ rootMargin: '800px' }
		);
		io.observe(sentinel);
		return () => io.disconnect();
	});

	function toggleFilters(): void {
		if (wide.current) panelOpen = !panelOpen;
		else sheetOpen = true;
	}

	async function apply(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		sheetOpen = false;
		await gallery.apply();
	}

	async function reset(): Promise<void> {
		sheetOpen = false;
		await gallery.resetFilters();
	}
</script>

{#snippet fields()}
	<form id="gallery-filters" class="fields" onsubmit={apply}>
		<label>
			<span>Type</span>
			<select bind:value={gallery.mediaKind}>
				<option value="">All media</option>
				<option value="image">Images</option>
				<option value="video">Videos</option>
				<option value="other">Other</option>
			</select>
		</label>
		<label>
			<span>Favorite</span>
			<select bind:value={gallery.favorite}>
				<option value="">All</option>
				<option value="true">Favorites</option>
				<option value="false">Not favorites</option>
			</select>
		</label>
		<label>
			<span>Workflow</span>
			<select bind:value={gallery.workflowId}>
				<option value="">Any workflow</option>
				{#each workflowNames.list as workflow (workflow.id)}
					<option value={workflow.id}>{workflow.name}</option>
				{/each}
			</select>
		</label>
		<label>
			<span>From</span>
			<input type="date" bind:value={gallery.createdAfter} />
		</label>
		<label>
			<span>Through</span>
			<input type="date" bind:value={gallery.createdBefore} />
		</label>
		<label>
			<span>Search in</span>
			<select
				value={gallery.searchField}
				onchange={(event) => gallery.setSearchField(event.currentTarget.value)}
			>
				<option value="any">Any saved value / workflow</option>
				<option value="prompt">Prompt / text inputs</option>
				<option value="model">Model / checkpoint inputs</option>
			</select>
		</label>
		<label>
			<span>Collection</span>
			<select bind:value={gallery.collectionId}>
				<option value="">Any collection</option>
				{#each gallery.collections as collection (collection.id)}
					<option value={collection.id}>{collection.name}</option>
				{/each}
			</select>
		</label>
		<label class="prompt">
			<span>Saved metadata</span>
			<input
				value={gallery.prompt}
				oninput={(event) => gallery.setPrompt(event.currentTarget.value)}
				list="gallery-suggestions"
				maxlength="500"
				autocomplete="off"
				placeholder="Search retained values"
				aria-describedby="search-scope"
			/>
			<datalist id="gallery-suggestions">
				{#each gallery.suggestions as value (value)}<option {value}></option>{/each}
			</datalist>
			<small id="search-scope"
				>Searches retained inputs of the latest 1,000 generations. Imported files have no metadata.</small
			>
		</label>
	</form>
{/snippet}

<div class="page gallery stack" style:--gap="var(--space-2)">
	<div class="row toolbar">
		<h1 class="grow">Gallery</h1>
		{#if comparing && picked.length === 2}
			<button type="button" class="btn btn-primary" onclick={() => (showCompare = true)}
				>Compare</button
			>
		{/if}
		{#if selecting && chosen.length}
			<button
				type="button"
				class="btn"
				disabled={gallery.downloadBusy}
				onclick={() => gallery.downloadZip(chosen, session.info?.csrf_token ?? null)}
				>{gallery.downloadBusy ? 'Preparing ZIP…' : 'Download ZIP'}</button
			>
			<button
				type="button"
				class="btn"
				disabled={gallery.collectionBusy}
				onclick={() => {
					collectionChoice = '';
					collectionName = '';
					collectionOpen = true;
				}}>Add to collection</button
			>
			{#if gallery.currentCollection}
				<button
					type="button"
					class="btn"
					disabled={gallery.collectionBusy}
					onclick={removeFromCollection}>Remove from collection</button
				>
			{/if}
			<button type="button" class="btn btn-danger" onclick={deleteChosen}
				>Delete ({chosen.length})</button
			>
		{/if}
		{#if selecting}
			<button type="button" class="btn" onclick={() => (chosen = gallery.items.map((i) => i.id))}
				>Select all</button
			>
		{/if}
		<div class="row tools">
			<button
				type="button"
				class="btn btn-icon"
				aria-pressed={selecting}
				aria-label={selecting ? 'Cancel select' : 'Select (or Ctrl/Shift+click a tile)'}
				title={selecting ? 'Cancel select' : 'Select (or Ctrl/Shift+click a tile)'}
				onclick={toggleSelect}
			>
				<Icon name="select" />
			</button>
			<button
				type="button"
				class="btn btn-icon"
				aria-pressed={comparing}
				aria-label={comparing ? `Cancel compare (${picked.length}/2)` : 'Compare two items'}
				title={comparing ? `Cancel compare (${picked.length}/2)` : 'Compare two items'}
				onclick={toggleCompare}
			>
				<Icon name="compare" />
				{#if comparing}<span class="badge badge-accent count">{picked.length}/2</span>{/if}
			</button>
			<button
				type="button"
				class="btn btn-icon"
				aria-label="Filters"
				title="Filters"
				aria-pressed={wide.current ? panelOpen : undefined}
				aria-haspopup={wide.current ? undefined : 'dialog'}
				onclick={toggleFilters}
			>
				<Icon name="filter" />
				{#if gallery.activeCount > 0}<span class="badge badge-accent count"
						>{gallery.activeCount}</span
					>{/if}
			</button>
			<button
				type="button"
				class="btn btn-icon"
				aria-label="View settings"
				title="View settings"
				aria-haspopup="dialog"
				onclick={() => (prefsOpen = true)}
			>
				<Icon name="sliders" />
			</button>
		</div>
	</div>

	{#if wide.current && panelOpen}
		<div class="card panel">
			{@render fields()}
			<div class="row actions">
				<button
					type="submit"
					form="gallery-filters"
					class="btn btn-primary"
					disabled={gallery.loading}>Apply filters</button
				>
				<button type="button" class="btn btn-ghost" onclick={reset} disabled={gallery.loading}
					>Reset</button
				>
			</div>
		</div>
	{/if}

	{#if !wide.current}
		<Sheet bind:open={sheetOpen} title="Filters" variant="sheet">
			{@render fields()}
			{#snippet footer()}
				<div class="row">
					<button type="button" class="btn grow" onclick={reset} disabled={gallery.loading}
						>Reset</button
					>
					<button
						type="submit"
						form="gallery-filters"
						class="btn btn-primary grow"
						disabled={gallery.loading}>Apply filters</button
					>
				</div>
			{/snippet}
		</Sheet>
	{/if}

	<Sheet bind:open={prefsOpen} title="View settings" variant="sheet">
		<div class="fields">
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
			<label class="check"
				><input type="checkbox" bind:checked={prefs.fit} /> Show whole image (no crop)</label
			>
			<label class="check"
				><input type="checkbox" bind:checked={prefs.badges} /> Show video badges</label
			>
			<label class="check"
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

	<Sheet bind:open={collectionOpen} title="Add to collection" variant="sheet">
		<form class="fields" onsubmit={addToCollection}>
			<label
				><span>Collection</span><select
					bind:value={collectionChoice}
					disabled={gallery.collectionBusy}
				>
					<option value="">Create new</option>
					{#each gallery.collections as collection (collection.id)}<option value={collection.id}
							>{collection.name}</option
						>{/each}
				</select></label
			>
			{#if !collectionChoice}<label
					><span>New collection name</span><input
						required
						maxlength="100"
						bind:value={collectionName}
						disabled={gallery.collectionBusy}
					/></label
				>{/if}
			<button class="btn btn-primary" disabled={gallery.collectionBusy || !chosen.length}
				>Add {chosen.length} selected</button
			>
			{#if gallery.error}<p class="error" role="alert">{gallery.error}</p>{/if}
		</form>
	</Sheet>

	{#if gallery.downloadMessage}<p class="muted" role="status">{gallery.downloadMessage}</p>{/if}
	{#if gallery.error}<p class="error" role="alert">{gallery.error}</p>{/if}

	<nav class="folders" aria-label="Gallery folders" title="Counts ignore filters. Dates are UTC.">
		{#if gallery.tree && gallery.tree.breadcrumbs.length > 1}
			<ol class="breadcrumbs">
				{#each gallery.tree.breadcrumbs as crumb, i (crumb.path)}
					<li>
						{#if i > 0}<Icon name="chevron-right" size={14} />{/if}
						{#if crumb.path === gallery.tree.path}
							<span aria-current="page">{folderName(crumb.path, crumb.name)}</span>
						{:else}
							<button type="button" onclick={() => gallery.navigate(crumb.path)}
								>{folderName(crumb.path, crumb.name)}</button
							>
						{/if}
					</li>
				{/each}
			</ol>
		{/if}
		{#if gallery.tree?.children.length}
			<div class="folder-list">
				{#each gallery.tree.children as folder (folder.path)}
					<button type="button" class="chip" onclick={() => gallery.navigate(folder.path)}>
						{folderName(folder.path, folder.name)} <span class="muted">{folder.count}</span>
					</button>
				{/each}
			</div>
		{/if}
	</nav>
	{#if gallery.folderPath.startsWith('Collections/')}
		<form
			class="row folder-list"
			onsubmit={(event) => {
				event.preventDefault();
				void gallery.changeCollection('PUT', gallery.currentCollection, { name: renameName });
			}}
		>
			<label
				><span>Rename collection</span><input
					required
					maxlength="100"
					bind:value={renameName}
				/></label
			>
			<button class="btn" disabled={gallery.collectionBusy}>Rename</button>
			<button
				type="button"
				class="btn btn-danger"
				disabled={gallery.collectionBusy}
				onclick={() => {
					if (confirm('Delete this collection? Its media will remain in the gallery.'))
						void gallery.changeCollection('DELETE', gallery.currentCollection);
				}}>Delete collection</button
			>
		</form>
	{/if}

	{#if gallery.loading && gallery.items.length === 0}
		<p class="muted">Loading…</p>
	{:else if gallery.items.length === 0}
		<p class="muted">No media matches these filters.</p>
	{/if}
	<ul class="grid" style:--tile={`${tile}px`} aria-busy={gallery.loading}>
		{#each gridEntries as entry (entry.key)}
			<li
				class={entry.item ? 'tile' : 'group-header'}
				class:fit={!!entry.item && prefs.fit}
				class:unavailable={!!entry.item && gallery.isUnavailable(entry.item)}
				class:picked={!!entry.item &&
					(picked.some((p) => p.id === entry.item?.id) || chosen.includes(entry.item.id))}
				animate:flip={{ duration: prefersReducedMotion.current ? 0 : 220, easing: cubicOut }}
				in:fade={{ duration: prefersReducedMotion.current ? 0 : 140 }}
				out:fade={{ duration: prefersReducedMotion.current ? 0 : 100 }}
			>
				{#if entry.item}
					{@const item = entry.item}
					{@const unavailable = gallery.isUnavailable(item)}
					<button
						class="preview"
						type="button"
						aria-label={`Open ${item.filename}`}
						title={item.filename}
						onclick={(event) => open(item, event)}
					>
						{#if unavailable}
							<span class="placeholder">File unavailable</span>
						{:else if item.media_kind !== 'other' && !gallery.thumbnailMissing[item.id]}
							<img
								src={`/api/media/${item.id}/thumbnail`}
								alt=""
								loading="lazy"
								onerror={() => gallery.markThumbnailMissing(item.id)}
							/>
						{:else}
							<span class="placeholder">{item.media_kind === 'video' ? 'Video' : 'File'}</span>
						{/if}
						{#if item.media_kind === 'video' && prefs.badges}
							<span class="badge video-badge"><Icon name="video" size={14} /> Video</span>
						{/if}
					</button>
					<button
						class="favorite btn-icon"
						class:on={item.favorite}
						type="button"
						aria-pressed={!!item.favorite}
						aria-label={item.favorite
							? `Remove ${item.filename} from favorites`
							: `Add ${item.filename} to favorites`}
						onclick={() => gallery.toggleFavorite(item)}
					>
						<Star filled={!!item.favorite} />
					</button>
				{:else if entry.group}
					<h2>{folderName(entry.group, entry.group)}</h2>
				{/if}
			</li>
		{/each}
	</ul>
	{#if gallery.canLoadMore}
		<div bind:this={sentinel} aria-hidden="true"></div>
		<button
			class="btn more"
			type="button"
			onclick={() => gallery.load(false)}
			disabled={gallery.loading}
		>
			{gallery.loading ? 'Loading…' : 'Load more'}
		</button>
	{/if}
</div>

{#if showCompare && picked.length === 2}
	<Compare
		items={[picked[0], picked[1]]}
		onclose={() => {
			showCompare = false;
			comparing = false;
			picked = [];
		}}
	/>
{/if}

{#if gallery.selected}
	<Viewer {gallery} />
{/if}

<style>
	.tools {
		gap: var(--space-1);
	}
	.tools .btn[aria-pressed='true'] {
		color: var(--color-accent);
		background: var(--color-accent-soft);
		border-color: transparent;
	}
	.tools .btn {
		position: relative;
	}
	.count {
		position: absolute;
		top: -0.4rem;
		right: -0.4rem;
		font-size: 0.65rem;
		padding: 0 0.3rem;
		pointer-events: none;
	}

	.folders {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.breadcrumbs {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		list-style: none;
		margin: 0;
		padding: 0;
		font-size: var(--text-sm);
		color: var(--color-text-muted);
	}
	.breadcrumbs li {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		margin-right: var(--space-1);
	}
	.breadcrumbs button {
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		cursor: pointer;
	}
	.breadcrumbs button:hover {
		color: var(--color-text);
		text-decoration: underline;
	}
	.breadcrumbs [aria-current] {
		color: var(--color-text);
		font-weight: 600;
	}
	.folder-list {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
	}
	.folder-list button {
		max-width: 100%;
		overflow-wrap: anywhere;
	}
	h1 {
		margin: 0;
	}
	/* The grid uses the whole content width; the base .page caps it at 72rem. */
	.gallery {
		max-width: none;
	}

	.fields {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
		gap: var(--space-2) var(--space-3);
	}
	.fields label {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-width: 0;
		font-size: var(--text-sm);
		color: var(--color-text-muted);
	}
	.fields .check {
		flex-direction: row;
		align-items: center;
		gap: var(--space-2);
	}
	.fields .prompt {
		grid-column: 1 / -1;
	}
	.fields small {
		font-size: var(--text-xs);
	}
	/* Inside the phone sheet: single column. */
	:global(dialog) .fields {
		grid-template-columns: 1fr;
	}
	.actions {
		margin-top: var(--space-2);
	}

	.grid {
		user-select: none; /* Shift+click range selection must not highlight text */
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(var(--tile, 10rem), 1fr));
		gap: var(--space-2);
	}
	.group-header {
		grid-column: 1 / -1;
		margin-top: var(--space-2);
		overflow-wrap: anywhere;
	}
	.group-header h2 {
		margin: 0;
		font-size: var(--text-base);
	}
	.tile {
		position: relative;
		min-width: 0;
		overflow: hidden;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-surface-2);
		content-visibility: auto;
		contain-intrinsic-size: auto var(--tile, 10rem);
	}
	.tile.fit img {
		object-fit: contain;
	}
	.tile.picked {
		outline: 3px solid var(--color-accent);
	}
	.tile.unavailable {
		border-color: var(--color-danger);
	}
	.preview {
		display: block;
		position: relative;
		width: 100%;
		padding: 0;
		border: 0;
		background: transparent;
		color: inherit;
		cursor: pointer;
	}
	.preview img,
	.placeholder {
		display: flex;
		width: 100%;
		aspect-ratio: 1;
		object-fit: cover;
		align-items: center;
		justify-content: center;
		text-align: center;
		padding: var(--space-2);
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}
	.preview:hover img {
		filter: brightness(1.08);
	}
	.video-badge {
		position: absolute;
		left: var(--space-1);
		bottom: var(--space-1);
		color: #fff;
		background: rgb(0 0 0 / 0.65);
	}
	.favorite {
		position: absolute;
		top: var(--space-1);
		right: var(--space-1);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: var(--control-h);
		height: var(--control-h);
		padding: 0;
		border: 0;
		border-radius: 50%;
		color: #fff;
		background: rgb(0 0 0 / 0.5);
		cursor: pointer;
		backdrop-filter: blur(4px);
	}
	.favorite.on {
		color: var(--color-accent);
		background: rgb(0 0 0 / 0.7);
	}

	.more {
		align-self: center;
		margin-top: var(--space-3);
	}
	.error {
		color: var(--color-danger);
	}
</style>
