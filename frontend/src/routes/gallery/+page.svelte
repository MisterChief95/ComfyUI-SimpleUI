<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { GalleryState } from '$lib/media/gallery.svelte';

	const gallery = new GalleryState();

	onMount(() => {
		gallery.apply();
		gallery.connect();
	});
	onDestroy(() => gallery.dispose());

	function formatDate(value: number): string {
		return new Date(value).toLocaleString();
	}
</script>

<h1>Gallery</h1>

<form class="filters" onsubmit={(event) => { event.preventDefault(); gallery.apply(); }}>
	<label>
		Type
		<select bind:value={gallery.mediaKind}>
			<option value="">All media</option>
			<option value="image">Images</option>
			<option value="video">Videos</option>
			<option value="other">Other</option>
		</select>
	</label>
	<label>
		Favorite
		<select bind:value={gallery.favorite}>
			<option value="">All</option>
			<option value="true">Favorites</option>
			<option value="false">Not favorites</option>
		</select>
	</label>
	<label>
		Workflow ID
		<input bind:value={gallery.workflowId} placeholder="Any workflow" />
	</label>
	<label>
		From
		<input type="date" bind:value={gallery.createdAfter} />
	</label>
	<label>
		Through
		<input type="date" bind:value={gallery.createdBefore} />
	</label>
	<label class="prompt">
		Known prompt
		<input bind:value={gallery.prompt} maxlength="500" placeholder="Search saved prompt values" />
	</label>
	<button type="submit" disabled={gallery.loading}>Apply filters</button>
</form>

{#if gallery.error}<p class="error" role="alert">{gallery.error}</p>{/if}

{#if gallery.selected}
	{@const item = gallery.selected}
	<section class="viewer" aria-label="Selected media">
		<div class="viewer-header">
			<div>
				<h2>{item.filename}</h2>
				<p>{formatDate(item.created_ms)} · {item.state}</p>
			</div>
			<button type="button" onclick={() => gallery.close()}>Close</button>
		</div>
		{#if gallery.isUnavailable(item)}
			<p class="missing" role="status">The file is unavailable. Its gallery and generation records are preserved.</p>
		{:else if item.media_kind === 'image'}
			<img src={`/api/media/${item.id}/file`} alt={item.filename} onerror={() => gallery.markUnavailable(item.id)} />
		{:else if item.media_kind === 'video'}
			<video controls preload="metadata" src={`/api/media/${item.id}/file`} onerror={() => gallery.markUnavailable(item.id)}>
				<track kind="captions" />
			</video>
		{:else}
			<p>Preview is not available for this file type.</p>
		{/if}
		<div class="actions">
			<button type="button" onclick={() => gallery.toggleFavorite(item)}>
				{item.favorite ? 'Remove favorite' : 'Add favorite'}
			</button>
			{#if !gallery.isUnavailable(item)}
				<a href={`/api/media/${item.id}/download`} download>Download original</a>
			{/if}
		</div>

		<section class="details">
			<h3>Generation details</h3>
			{#if !item.generation_id}
				<p>Workflow and prompt are unknown for this imported media.</p>
			{:else if gallery.detailLoading}
				<p>Loading generation…</p>
			{:else if gallery.detailError}
				<p class="error" role="alert">{gallery.detailError}</p>
			{:else if gallery.detail}
				<p>Status: <strong>{gallery.detail.status}</strong></p>
				<p>Output: {gallery.detail.output_state}</p>
				{#if gallery.detail.error}
					<pre class="error">{JSON.stringify(gallery.detail.error, null, 2)}</pre>
				{/if}
				{#if gallery.detail.effective_values}
					<details>
						<summary>Saved prompt and input values</summary>
						<pre>{JSON.stringify(gallery.detail.effective_values, null, 2)}</pre>
					</details>
					{#if gallery.detail.workflow_id}
						<a class="reuse" href={`/generation/${gallery.detail.workflow_id}?reuse=${gallery.detail.id}`}>Reuse as draft</a>
					{/if}
				{:else}
					<p>Saved prompt and workflow inputs are unavailable. The media and generation status remain.</p>
				{/if}
			{/if}
		</section>
	</section>
{/if}

{#if gallery.loading && gallery.items.length === 0}
	<p>Loading…</p>
{:else if gallery.items.length === 0}
	<p>No media matches these filters.</p>
{:else}
	<div class="grid">
		{#each gallery.items as item (item.id)}
			<article class:unavailable={gallery.isUnavailable(item)}>
				<button class="preview" type="button" onclick={() => gallery.select(item)}>
					{#if gallery.isUnavailable(item)}
						<span class="placeholder">File unavailable</span>
					{:else if item.media_kind === 'image' && !gallery.thumbnailMissing[item.id]}
						<img src={`/api/media/${item.id}/thumbnail`} alt="" loading="lazy" onerror={() => gallery.markThumbnailMissing(item.id)} />
					{:else}
						<span class="placeholder">{item.media_kind === 'video' ? 'Video' : 'File'}</span>
					{/if}
					<span class="filename">{item.filename}</span>
				</button>
				<button class="favorite" type="button" aria-label={item.favorite ? `Remove ${item.filename} from favorites` : `Add ${item.filename} to favorites`} onclick={() => gallery.toggleFavorite(item)}>
					{item.favorite ? '★' : '☆'}
				</button>
			</article>
		{/each}
	</div>
	{#if gallery.nextCursor}
		<button class="more" type="button" onclick={() => gallery.load(false)} disabled={gallery.loading}>
			{gallery.loading ? 'Loading…' : 'Load more'}
		</button>
	{/if}
{/if}

<style>
	.filters {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
		gap: var(--space-2);
		margin-bottom: var(--space-4);
		padding: var(--space-3);
		background: var(--color-bg-elevated);
		border-radius: var(--radius);
	}

	.filters label { display: flex; flex-direction: column; gap: var(--space-1); font-size: 0.875rem; }
	.filters .prompt { grid-column: span 2; }
	.filters input, .filters select, .filters button, .viewer button, .viewer a, .more {
		min-height: var(--touch-target);
		padding: 0 var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-bg);
	}
	.filters button, .more { align-self: end; background: var(--color-accent); color: var(--color-accent-text); border: none; cursor: pointer; }

	.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: var(--space-3); }
	.grid article { position: relative; min-width: 0; border: 1px solid var(--color-border); border-radius: var(--radius); overflow: hidden; background: var(--color-bg-elevated); }
	.grid article.unavailable { border-color: var(--color-danger); }
	.preview { width: 100%; padding: 0; border: 0; background: transparent; color: inherit; cursor: pointer; text-align: left; }
	.preview img, .placeholder { display: flex; width: 100%; aspect-ratio: 1; object-fit: cover; align-items: center; justify-content: center; background: var(--color-bg); color: var(--color-text-muted); }
	.filename { display: block; padding: var(--space-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.favorite { position: absolute; top: var(--space-1); right: var(--space-1); min-width: var(--touch-target); min-height: var(--touch-target); border: 1px solid var(--color-border); border-radius: 50%; background: var(--color-bg); cursor: pointer; font-size: 1.25rem; }

	.viewer { margin-bottom: var(--space-4); padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius); }
	.viewer-header, .actions { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); flex-wrap: wrap; }
	.viewer-header h2, .viewer-header p { margin: 0; }
	.viewer-header p { color: var(--color-text-muted); font-size: 0.875rem; }
	.viewer > img, .viewer > video { display: block; max-width: 100%; max-height: 70dvh; margin: var(--space-3) auto; }
	.actions { justify-content: flex-start; margin: var(--space-3) 0; }
	.actions a, .reuse { display: inline-flex; align-items: center; color: inherit; text-decoration: none; }
	.details { border-top: 1px solid var(--color-border); }
	.details pre { white-space: pre-wrap; overflow-wrap: anywhere; }
	.missing, .error { color: var(--color-danger); }
	.more { display: block; margin: var(--space-4) auto 0; }

	@media (max-width: 520px) { .filters .prompt { grid-column: span 1; } }
</style>
