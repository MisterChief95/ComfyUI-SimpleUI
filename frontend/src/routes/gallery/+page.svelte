<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { MediaQuery } from 'svelte/reactivity';
	import { GalleryState } from '$lib/media/gallery.svelte';
	import { workflowNames } from '$lib/media/workflowNames.svelte';
	import { settingsState } from '$lib/settings.svelte';
	import Compare from '$lib/media/Compare.svelte';
	import type { MediaInfo } from '$lib/contracts';
	import Viewer from '$lib/media/Viewer.svelte';
	import Star from '$lib/media/Star.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';

	const gallery = new GalleryState();
	const wide = new MediaQuery('min-width: 768px');

	let panelOpen = $state(false);
	let sheetOpen = $state(false);
	let comparing = $state(false);
	let picked = $state<MediaInfo[]>([]);
	let showCompare = $state(false);

	function open(item: MediaInfo): void {
		if (!comparing) return void gallery.select(item);
		picked = picked.some((p) => p.id === item.id) ? picked.filter((p) => p.id !== item.id) : [...picked, item].slice(-2);
	}
	function toggleCompare(): void {
		comparing = !comparing;
		picked = [];
	}

	// Thumbnail size is a profile setting; it only changes the grid's minimum column width.
	const tile = $derived(
		{ small: '7.5rem', large: '13rem' }[String(settingsState.data?.profile.thumbnail_size)] ?? '10rem'
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
		return () => { disposed = true; };
	});
	onDestroy(() => gallery.dispose());

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
		<label class="prompt">
			<span>Known prompt</span>
			<input bind:value={gallery.prompt} maxlength="500" placeholder="Search saved prompt values" />
		</label>
	</form>
{/snippet}

<div class="page stack" style:--gap="var(--space-2)">
	<div class="row">
		<h1 class="grow">Gallery</h1>
		{#if comparing && picked.length === 2}
			<button type="button" class="btn btn-primary" onclick={() => (showCompare = true)}>Compare</button>
		{/if}
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
			{#if gallery.activeCount > 0}<span class="badge badge-accent">{gallery.activeCount}</span>{/if}
		</button>
	</div>

	{#if wide.current && panelOpen}
		<div class="card panel">
			{@render fields()}
			<div class="row actions">
				<button type="submit" form="gallery-filters" class="btn btn-primary" disabled={gallery.loading}>Apply filters</button>
				<button type="button" class="btn btn-ghost" onclick={reset} disabled={gallery.loading}>Reset</button>
			</div>
		</div>
	{/if}

	{#if !wide.current}
		<Sheet bind:open={sheetOpen} title="Filters" variant="sheet">
			{@render fields()}
			{#snippet footer()}
				<div class="row">
					<button type="button" class="btn grow" onclick={reset} disabled={gallery.loading}>Reset</button>
					<button type="submit" form="gallery-filters" class="btn btn-primary grow" disabled={gallery.loading}>Apply filters</button>
				</div>
			{/snippet}
		</Sheet>
	{/if}

	{#if gallery.error}<p class="error" role="alert">{gallery.error}</p>{/if}

	{#if gallery.loading && gallery.items.length === 0}
		<p class="muted">Loading…</p>
	{:else if gallery.items.length === 0}
		<p class="muted">No media matches these filters.</p>
	{:else}
		<ul class="grid" style:--tile={tile}>
			{#each gallery.items as item (item.id)}
				{@const unavailable = gallery.isUnavailable(item)}
				<li class="tile" class:unavailable class:picked={picked.some((p) => p.id === item.id)}>
					<button class="preview" type="button" aria-label={`Open ${item.filename}`} title={item.filename} onclick={() => open(item)}>
						{#if unavailable}
							<span class="placeholder">File unavailable</span>
						{:else if item.media_kind === 'image' && !gallery.thumbnailMissing[item.id]}
							<img src={`/api/media/${item.id}/thumbnail`} alt="" loading="lazy" onerror={() => gallery.markThumbnailMissing(item.id)} />
						{:else}
							<span class="placeholder">{item.media_kind === 'video' ? 'Video' : 'File'}</span>
						{/if}
						{#if item.media_kind === 'video'}
							<span class="badge video-badge"><Icon name="video" size={14} /> Video</span>
						{/if}
					</button>
					<button
						class="favorite btn-icon"
						class:on={item.favorite}
						type="button"
						aria-pressed={!!item.favorite}
						aria-label={item.favorite ? `Remove ${item.filename} from favorites` : `Add ${item.filename} to favorites`}
						onclick={() => gallery.toggleFavorite(item)}
					>
						<Star filled={!!item.favorite} />
					</button>
				</li>
			{/each}
		</ul>
		{#if gallery.nextCursor}
			<button class="btn more" type="button" onclick={() => gallery.load(false)} disabled={gallery.loading}>
				{gallery.loading ? 'Loading…' : 'Load more'}
			</button>
		{/if}
	{/if}
</div>

{#if showCompare && picked.length === 2}
	<Compare items={[picked[0], picked[1]]} onclose={() => { showCompare = false; comparing = false; picked = []; }} />
{/if}

{#if gallery.selected}
	<Viewer {gallery} />
{/if}

<style>
	h1 {
		margin: 0;
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
	.tile {
		position: relative;
		min-width: 0;
		overflow: hidden;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-surface-2);
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
