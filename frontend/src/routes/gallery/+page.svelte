<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { MediaQuery } from 'svelte/reactivity';
	import { GalleryState } from '$lib/media/gallery.svelte';
	import { workflowNames } from '$lib/media/workflowNames.svelte';
	import { settingsState } from '$lib/settings.svelte';
	import { session } from '$lib/session.svelte';
	import Compare from '$lib/media/Compare.svelte';
	import type { MediaInfo } from '$lib/contracts';
	import Viewer from '$lib/media/Viewer.svelte';
	import { ViewPrefs, type GallerySort } from '$lib/media/viewPrefs.svelte';
	import Star from '$lib/media/Star.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';

	const prefs = new ViewPrefs();
	const gallery = new GalleryState(prefs);
	const wide = new MediaQuery('min-width: 768px');

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

	function open(item: MediaInfo): void {
		if (selecting) {
			chosen = chosen.includes(item.id)
				? chosen.filter((id) => id !== item.id)
				: [...chosen, item.id];
			return;
		}
		if (!comparing) return void gallery.select(item);
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

	// This device's size choice wins over the profile setting; either only changes the grid's minimum column width.
	const tile = $derived(
		{ small: '7.5rem', large: '13rem' }[
			prefs.size || String(settingsState.data?.profile.thumbnail_size)
		] ?? '10rem'
	);

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
		</label>
		<p id="search-scope" class="muted">
			Latest 1,000 generations; retained scalar controls only. Prompt and Model use stored input
			names. Imported files have no saved metadata. Large snapshots and controls after the first 200
			are skipped.
		</p>
	</form>
{/snippet}

<div class="page gallery stack" style:--gap="var(--space-2)">
	<div class="row">
		<h1 class="grow">Gallery</h1>
		<button
			type="button"
			class="btn-icon"
			aria-label="View settings"
			onclick={() => (prefsOpen = true)}
		>
			<Icon name="settings" />
		</button>
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
				>All</button
			>
		{/if}
		<button type="button" class="btn" aria-pressed={selecting} onclick={toggleSelect}>
			{selecting ? 'Cancel select' : 'Select'}
		</button>
		<button type="button" class="btn" aria-pressed={comparing} onclick={toggleCompare}>
			{comparing ? `Cancel compare (${picked.length}/2)` : 'Compare'}
		</button>
		<button
			type="button"
			class="btn"
			aria-expanded={wide.current ? panelOpen : undefined}
			aria-haspopup={wide.current ? undefined : 'dialog'}
			onclick={toggleFilters}
		>
			Filters
			{#if gallery.activeCount > 0}<span class="badge badge-accent">{gallery.activeCount}</span
				>{/if}
		</button>
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
				<span>Thumbnail size</span>
				<select bind:value={prefs.size}>
					<option value="">Profile default</option>
					<option value="small">Small</option>
					<option value="medium">Medium</option>
					<option value="large">Large</option>
				</select>
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

	<nav class="folders" aria-label="Gallery folders">
		<div class="row breadcrumbs">
			{#each gallery.tree?.breadcrumbs ?? [{ path: '', name: 'All media' }] as crumb (crumb.path)}
				<button
					type="button"
					class="btn btn-ghost"
					aria-current={crumb.path === gallery.folderPath ? 'page' : undefined}
					onclick={() => gallery.navigate(crumb.path)}>{crumb.name}</button
				>
			{/each}
		</div>
		{#if gallery.tree}
			<div class="row folder-list">
				{#each gallery.tree.children as folder (folder.path)}
					<button type="button" class="btn" onclick={() => gallery.navigate(folder.path)}>
						{folder.name} <span class="badge">{folder.count}</span>
					</button>
				{/each}
			</div>
			<p class="muted folder-note">
				Folder counts include all visible media before filters. Dates use UTC.
			</p>
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
	{:else}
		<ul class="grid" style:--tile={tile}>
			{#each gallery.items as item, index (item.id)}
				{@const group = gallery.groupHeader(index)}
				{#if group}<li class="group-header"><h2>{group}</h2></li>{/if}
				{@const unavailable = gallery.isUnavailable(item)}
				<li
					class="tile"
					class:fit={prefs.fit}
					class:unavailable
					class:picked={picked.some((p) => p.id === item.id) || chosen.includes(item.id)}
				>
					<button
						class="preview"
						type="button"
						aria-label={`Open ${item.filename}`}
						title={item.filename}
						onclick={() => open(item)}
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
	.breadcrumbs,
	.folder-list {
		flex-wrap: wrap;
	}
	.breadcrumbs button,
	.folder-list button {
		max-width: 100%;
		overflow-wrap: anywhere;
	}
	.folder-note {
		font-size: var(--text-sm);
		margin: var(--space-1) 0;
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
	/* Inside the phone sheet: single column. */
	:global(dialog) .fields {
		grid-template-columns: 1fr;
	}
	.actions {
		margin-top: var(--space-3);
	}

	.grid {
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
