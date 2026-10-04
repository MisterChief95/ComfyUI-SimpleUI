<script lang="ts">
	// Full-screen media viewer on a native <dialog> (focus trap + Escape).
	// Mount it only while an item is selected: {#if gallery.selected}<Viewer {gallery} />{/if}
	// Navigation: prev/next buttons, ArrowLeft/ArrowRight, horizontal swipe on images,
	// and (opt-in, per device) vertical swipe: up = next, down = previous.
	import { onMount } from 'svelte';
	import { api, describeApiError } from '$lib/api';
	import { startSlideshow } from './viewerPlayback';
	import { on } from 'svelte/events';
	import { MediaQuery, SvelteSet } from 'svelte/reactivity';
	import type { MediaInfo } from '$lib/contracts';
	import Icon from '$lib/ui/Icon.svelte';
	import Thumbnail from '$lib/media/Thumbnail.svelte';
	import ResizeHandle from '$lib/ui/ResizeHandle.svelte';
	import { readPanelWidth, readFlag, writeFlag, readStored, writeStored } from '$lib/ui/storage';
	import { isolateInput } from '$lib/ui/isolateInput';
	import { settingsState } from '$lib/settings.svelte';
	import GraphJson from './GraphJson.svelte';
	import MetaValues from './MetaValues.svelte';
	import GenerationSummary from './GenerationSummary.svelte';
	import Star from './Star.svelte';
	import { formatDate } from './format';
	import type { GalleryState } from './gallery.svelte';

	let { gallery }: { gallery: GalleryState } = $props();

	const wide = new MediaQuery('min-width: 768px');
	let dialog = $state<HTMLDialogElement>();
	// Details side panel (desktop) or bottom panel (phone). Closed on phone by default.
	let showDetails = $state(readFlag('simpleui.viewerDetails', wide.current));
	let showStrip = $state(readFlag('simpleui.viewerStrip', true));
	let vertical = $state(readFlag('simpleui.viewerVertical', false));

	// Desktop details width in px; null = 22rem. Per-device preference. Below 768px the panel is a bottom sheet and not resizable.
	const WIDTH_KEY = 'simpleui.viewerDetailsWidth';
	let body = $state<HTMLElement>();
	let detailsW = $state<number | null>(readPanelWidth(WIDTH_KEY));
	let fullscreen = $state(false);
	let playbackError = $state<string | null>(null);
	let playing = $state(false);
	const savedSeconds = Number(readStored('simpleui.slideshowSeconds'));
	let seconds = $state([2, 5, 10, 30].includes(savedSeconds) ? savedSeconds : 5);
	type DuplicateResult = {
		groups: { byte_size: string; count: number; items: MediaInfo[] }[];
		unavailable: number;
	};
	let duplicates = $state<DuplicateResult | null>(null);
	let duplicatesLoading = $state(false);
	let duplicatesError = $state<string | null>(null);

	async function toggleFullscreen(): Promise<void> {
		playbackError = null;
		try {
			if (document.fullscreenElement === body) await document.exitFullscreen();
			else await body?.requestFullscreen();
		} catch {
			playbackError = 'Fullscreen is unavailable in this browser or window.';
		}
	}
	function closeViewer(): void {
		playing = false;
		if (document.fullscreenElement === body) void document.exitFullscreen().catch(() => {});
		gallery.close();
	}
	$effect(() => {
		if (!playing) return;
		return startSlideshow(
			async () => {
				if (!hasNext) return false;
				const before = gallery.selected?.id;
				previous = shownItem;
				await gallery.step(1);
				return gallery.selected?.id !== before;
			},
			seconds,
			() => {
				playing = false;
			}
		);
	});
	$effect(() => {
		void item?.id;
		duplicates = null;
		duplicatesError = null;
		duplicatesLoading = false;
	});
	async function findDuplicates(): Promise<void> {
		const id = item?.id;
		if (!id) return;
		duplicatesLoading = true;
		duplicatesError = null;
		try {
			const result = await api<DuplicateResult>(
				`/media/duplicates?media_id=${encodeURIComponent(id)}`
			);
			if (gallery.selected?.id === id) duplicates = result;
		} catch (cause) {
			if (gallery.selected?.id === id) duplicatesError = describeApiError(cause);
		} finally {
			if (gallery.selected?.id === id) duplicatesLoading = false;
		}
	}
	async function deleteDuplicate(entry: MediaInfo): Promise<void> {
		if (
			!confirm(
				`Delete “${entry.filename}” from the gallery? Captured copies are deleted; indexed originals are preserved.`
			)
		)
			return;
		if (await gallery.deleteMany([entry.id])) await findDuplicates();
	}

	const item = $derived(gallery.selected);
	const index = $derived(gallery.selectedIndex);
	const hasPrev = $derived(index >= 0 && (index > 0 || gallery.canLoadPrevious));
	const hasNext = $derived(index >= 0 && (index < gallery.items.length - 1 || gallery.canLoadMore));
	const stripItems = $derived(
		gallery.items.slice(Math.max(0, index - 30), Math.max(0, index) + 31)
	);
	const decoded = new SvelteSet<string>();
	let previous = $state.raw<MediaInfo | null>(null);
	// Keep the outgoing image visible until the requested image has decoded.
	const shownItem = $derived(
		item?.media_kind === 'image' &&
			!gallery.isUnavailable(item) &&
			!decoded.has(item.id) &&
			previous?.media_kind === 'image' &&
			!gallery.isUnavailable(previous)
			? (previous ?? item)
			: item
	);
	const shownIndex = $derived(gallery.items.findIndex((entry) => entry.id === shownItem?.id));
	const slides = $derived.by(() => {
		if (!item) return [];
		const nearby = index < 0 ? [item] : gallery.items.slice(Math.max(0, index - 2), index + 3);
		const images = nearby.filter((entry) => entry.id === item.id || entry.media_kind === 'image');
		// A distant thumbnail jump also retains one outgoing image, so at most six frames stay mounted.
		const outgoing = previous;
		if (outgoing?.media_kind === 'image' && !images.some((entry) => entry.id === outgoing.id))
			images.push(outgoing);
		return images;
	});

	function decodeImage(image: HTMLImageElement, id: string): () => void {
		let active = true;
		void image.decode().then(
			() => {
				if (active) decoded.add(id);
			},
			() => {
				if (active) gallery.markUnavailable(id);
			}
		);
		return () => {
			active = false;
			decoded.delete(id);
		};
	}

	function step(delta: -1 | 1): void {
		previous = shownItem;
		void gallery.step(delta);
	}
	function select(entry: MediaInfo): void {
		previous = shownItem;
		void gallery.select(entry);
	}

	// Images at natural size (scrollable) instead of scaled to fit the stage.
	let full = $state(false);
	let strip = $state<HTMLElement>();

	// Zoom/pan in full-size mode: the image is centered in the stage and moved by (x, y) from there, then scaled about its centre.
	let stage = $state<HTMLElement>();
	let view = $state({ s: 1, x: 0, y: 0 });
	const pointers = new Map<number, { x: number; y: number }>();

	$effect(() => {
		void item?.id;
		void full;
		view = { s: 1, x: 0, y: 0 };
	});

	// Scale by `k` keeping the point (cx, cy) (relative to the stage centre) fixed.
	function zoomAt(k: number, cx: number, cy: number): void {
		const s = Math.min(Math.max(view.s * k, 0.1), 16);
		const f = s / view.s;
		view = { s, x: cx - (cx - view.x) * f, y: cy - (cy - view.y) * f };
	}
	function fromCentre(clientX: number, clientY: number): [number, number] {
		const r = stage!.getBoundingClientRect();
		return [clientX - (r.left + r.width / 2), clientY - (r.top + r.height / 2)];
	}
	function zoomDown(event: PointerEvent): void {
		if (!full || (event.target as HTMLElement).closest('button')) return;
		stage?.setPointerCapture(event.pointerId);
		pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
	}
	function zoomMove(event: PointerEvent): void {
		const prev = pointers.get(event.pointerId);
		if (!full || !prev) return;
		const now = { x: event.clientX, y: event.clientY };
		const other = [...pointers].find(([id]) => id !== event.pointerId)?.[1];
		pointers.set(event.pointerId, now);
		if (!other) {
			view = { ...view, x: view.x + now.x - prev.x, y: view.y + now.y - prev.y };
			return;
		}
		// Pinch: zoom by the change in finger distance, pan by the midpoint's movement.
		const k =
			Math.hypot(now.x - other.x, now.y - other.y) /
			(Math.hypot(prev.x - other.x, prev.y - other.y) || 1);
		const [cx, cy] = fromCentre((now.x + other.x) / 2, (now.y + other.y) / 2);
		zoomAt(k, cx, cy);
		view = { ...view, x: view.x + (now.x - prev.x) / 2, y: view.y + (now.y - prev.y) / 2 };
	}
	function zoomUp(event: PointerEvent): void {
		pointers.delete(event.pointerId);
	}
	// Needs a non-passive listener so the page does not scroll while zooming.
	$effect(() => {
		const el = stage;
		if (!el || !full) return;
		const wheel = (event: WheelEvent): void => {
			event.preventDefault();
			zoomAt(Math.exp(-event.deltaY * 0.002), ...fromCentre(event.clientX, event.clientY));
		};
		el.addEventListener('wheel', wheel, { passive: false });
		return () => el.removeEventListener('wheel', wheel);
	});

	onMount(() => {
		dialog?.showModal();
		return () => {
			if (document.fullscreenElement === body) void document.exitFullscreen().catch(() => {});
		};
	});

	// Keep the current thumbnail in view in the strip.
	$effect(() => {
		void item?.id;
		void showStrip;
		strip
			?.querySelector('[aria-current="true"]')
			?.scrollIntoView({ inline: 'center', block: 'nearest' });
	});

	function onkeydown(event: KeyboardEvent): void {
		if (event.defaultPrevented || event.isComposing) return;
		const target = event.target as HTMLElement;
		// Let video scrubbing and form fields keep their own arrow keys.
		if (
			target.closest('video, input, select, textarea, [role="textbox"]') ||
			target.isContentEditable
		)
			return;
		if (event.key.toLowerCase() === 'f' && !event.ctrlKey && !event.metaKey && !event.altKey) {
			event.preventDefault();
			void toggleFullscreen();
		} else if (event.key === ' ' && !target.closest('button, a')) {
			event.preventDefault();
			playing = !playing;
		} else if (event.key === 'ArrowLeft' && hasPrev) {
			event.preventDefault();
			step(-1);
		} else if (event.key === 'ArrowRight' && hasNext) {
			event.preventDefault();
			step(1);
		}
	}

	let swipe: { x: number; y: number } | null = null;

	let downAt = { x: 0, y: 0 };

	// A tap on the blank stage (not the image, buttons, or the end of a swipe/pan) closes the viewer.
	function stageClick(event: MouseEvent): void {
		if (
			full ||
			(event.target !== stage && !(event.target as HTMLElement).classList.contains('media-frame'))
		)
			return;
		if (Math.hypot(event.clientX - downAt.x, event.clientY - downAt.y) < 10) dialog?.close();
	}

	function pointerdown(event: PointerEvent): void {
		downAt = { x: event.clientX, y: event.clientY };
		// Dragging a video's seek bar is not a swipe.
		const target = event.target as HTMLElement;
		// A video's controls (seek bar) sit along its bottom edge; in vertical mode the rest of the video can still swipe.
		const onControls =
			target.tagName === 'VIDEO' &&
			(!vertical || event.clientY > target.getBoundingClientRect().bottom - 64);
		swipe =
			full || onControls || target.closest('button')
				? null
				: { x: event.clientX, y: event.clientY };
	}

	function pointerup(event: PointerEvent): void {
		if (!swipe) return;
		const dx = event.clientX - swipe.x;
		const dy = event.clientY - swipe.y;
		swipe = null;
		if (vertical && Math.abs(dy) >= 60 && Math.abs(dy) > Math.abs(dx) * 1.5) {
			if (dy < 0 && hasNext) step(1);
			else if (dy > 0 && hasPrev) step(-1);
			return;
		}
		if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
		if (dx > 0 && hasPrev) step(-1);
		else if (dx < 0 && hasNext) step(1);
	}

	const titleId = $props.id();
</script>

<svelte:document
	onfullscreenchange={() => {
		fullscreen = document.fullscreenElement === body;
	}}
/>

{#if item}
	<dialog
		{@attach (element) => on(element, 'keydown', onkeydown)}
		{@attach isolateInput}
		bind:this={dialog}
		aria-labelledby={titleId}
		onclose={closeViewer}
	>
		<div class="body" class:with-details={showDetails} bind:this={body}>
			<div class="view">
				<header>
					<div class="grow">
						<h2 id={titleId} title={item.filename}>{item.filename}</h2>
						<p class="muted meta">
							{formatDate(item.created_ms)}
							{#if index >= 0}· {index + 1} of {gallery.items.length}{gallery.canLoadMore ||
								gallery.canLoadPrevious
									? '+'
									: ''}{/if}
						</p>
					</div>
					<button
						type="button"
						class="btn btn-ghost btn-icon"
						class:fav={item.favorite}
						aria-label={item.favorite ? 'Remove favorite' : 'Add favorite'}
						aria-pressed={!!item.favorite}
						onclick={() => gallery.toggleFavorite(item)}
					>
						<Star filled={!!item.favorite} size={20} />
					</button>
					{#if gallery.detail?.workflow_id}
						{@const d = gallery.detail}
						{@const label = d.effective_values
							? 'Reuse settings'
							: 'Open workflow (saved values were cleared)'}
						<a
							class="btn btn-icon ctl"
							href={`/generation/${d.workflow_id}${d.effective_values ? `?reuse=${d.id}` : ''}`}
							aria-label={label}
							title={label}
						>
							<Icon name="generate" />
						</a>
					{/if}
					{#if !gallery.isUnavailable(item)}
						<a
							class="btn btn-icon ctl"
							href={`/api/media/${item.id}/download`}
							download
							aria-label="Download"
							title="Download"><Icon name="download" /></a
						>
					{/if}
					{#if item.media_kind === 'image' && !gallery.isUnavailable(item)}
						<button
							type="button"
							class="btn btn-icon ctl"
							aria-pressed={full}
							aria-label={full ? 'Fit to screen' : 'Full size'}
							title={full ? 'Fit to screen' : 'Full size'}
							onclick={() => (full = !full)}
						>
							<Icon name={full ? 'collapse' : 'expand'} />
						</button>
					{/if}
					<button
						type="button"
						class="btn btn-ghost"
						aria-pressed={fullscreen}
						title="Fullscreen (F)"
						onclick={toggleFullscreen}
					>
						{fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
					</button>
					<button
						type="button"
						class="btn btn-ghost"
						aria-pressed={playing}
						title="Slideshow (Space)"
						onclick={() => {
							playing = !playing;
						}}
					>
						{playing ? 'Pause' : 'Slideshow'}
					</button>
					<button
						type="button"
						class="btn btn-ghost"
						aria-expanded={showDetails}
						onclick={() => writeFlag('simpleui.viewerDetails', (showDetails = !showDetails))}
					>
						Details
					</button>
					<button
						type="button"
						class="btn btn-ghost btn-icon"
						aria-label="Close viewer"
						onclick={() => dialog?.close()}
					>
						<Icon name="close" />
					</button>
				</header>
				{#if playbackError}<p class="error playback-error" role="alert">{playbackError}</p>{/if}
				{#if gallery.error}<p class="error" role="alert">{gallery.error}</p>{/if}
				{#if gallery.loading}<p class="muted" role="status">Loading media…</p>{/if}

				<div
					class="stage"
					class:full
					class:vertical
					aria-busy={item.id !== shownItem?.id}
					bind:this={stage}
					onpointerdown={(event) => {
						pointerdown(event);
						zoomDown(event);
					}}
					onpointermove={zoomMove}
					onpointerup={(event) => {
						pointerup(event);
						zoomUp(event);
					}}
					onpointercancel={(event) => {
						swipe = null;
						zoomUp(event);
					}}
					onclick={stageClick}
					ondblclick={() => (view = { s: 1, x: 0, y: 0 })}
					role="presentation"
				>
					{#if hasPrev && !full}
						<button
							type="button"
							class="nav prev btn btn-icon"
							aria-label="Previous"
							disabled={gallery.loading}
							onclick={() => step(-1)}
						>
							<Icon name="chevron-left" size={24} />
						</button>
					{/if}
					{#each slides as entry (entry.id)}
						<div
							class="media-frame"
							class:current={entry.id === shownItem?.id}
							aria-hidden={entry.id !== shownItem?.id}
							inert={entry.id !== shownItem?.id}
							style:--slide-offset={entry.id === shownItem?.id
								? 0
								: gallery.items.findIndex((candidate) => candidate.id === entry.id) < shownIndex
									? -1
									: 1}
						>
							{#if gallery.isUnavailable(entry)}
								<p class="missing" role="status">
									The file is unavailable. Its gallery and generation records are preserved.
								</p>
							{:else if entry.media_kind === 'image'}
								<img
									{@attach (image) => decodeImage(image, entry.id)}
									src={`/api/media/${entry.id}/file`}
									alt={entry.id === shownItem?.id ? entry.filename : ''}
									decoding="async"
									draggable="false"
									style:transform={full
										? `translate(${view.x}px, ${view.y}px) scale(${view.s})`
										: undefined}
									onerror={() => gallery.markUnavailable(entry.id)}
									data-lightbox-image={entry.id === shownItem?.id ? '' : undefined}
								/>
							{:else if entry.media_kind === 'video'}
								<video
									controls
									autoplay={settingsState.data?.profile.gallery_autoplay === true}
									muted={settingsState.data?.profile.gallery_autoplay === true}
									playsinline
									preload="metadata"
									src={`/api/media/${entry.id}/file`}
									onerror={() => gallery.markUnavailable(entry.id)}
								>
									<track kind="captions" />
								</video>
							{:else}
								<p class="muted">Preview is not available for this file type.</p>
							{/if}
						</div>
					{/each}
					{#if item.id !== shownItem?.id}
						<p class="image-loading" role="status">Loading image…</p>
					{/if}
					{#if hasNext && !full}
						<button
							type="button"
							class="nav next btn btn-icon"
							aria-label="Next"
							disabled={gallery.loading}
							onclick={() => step(1)}
						>
							<Icon name="chevron-right" size={24} />
						</button>
					{/if}
				</div>

				{#if gallery.items.length > 1}
					<div class="strip-wrap">
						<button
							type="button"
							class="btn btn-icon ctl fold"
							aria-label={showStrip ? 'Hide thumbnails' : 'Show thumbnails'}
							aria-expanded={showStrip}
							onclick={() => writeFlag('simpleui.viewerStrip', (showStrip = !showStrip))}
						>
							<Icon name={showStrip ? 'chevron-down' : 'chevron-up'} size={16} />
						</button>
						{#if showStrip}
							<div class="strip" role="group" aria-label="Items" bind:this={strip}>
								{#each stripItems as entry (entry.id)}
									<button
										type="button"
										class="thumb"
										aria-label={entry.filename}
										aria-current={entry.id === item.id}
										onclick={() => select(entry)}
									>
										<Thumbnail
											item={entry}
											missing={gallery.thumbnailMissing[entry.id]}
											onerror={() => gallery.markThumbnailMissing(entry.id)}
										/>
									</button>
								{/each}
							</div>
						{/if}
					</div>
				{/if}
			</div>

			{#if showDetails}
				<ResizeHandle
					class="divider"
					bind:width={detailsW}
					container={body}
					label="Resize details panel"
					storageKey={WIDTH_KEY}
					min={240}
					remaining={320}
					defaultWidth={352}
				/>

				<aside
					class="details"
					aria-label="Generation details"
					style:width={wide.current && detailsW ? `${detailsW}px` : undefined}
				>
					<h3>Generation details</h3>
					<label class="row"
						>Slideshow interval
						<select
							bind:value={seconds}
							onchange={() => writeStored('simpleui.slideshowSeconds', String(seconds))}
						>
							{#each [2, 5, 10, 30] as interval (interval)}<option value={interval}
									>{interval} seconds</option
								>{/each}
						</select>
					</label>
					<button type="button" class="btn" disabled={duplicatesLoading} onclick={findDuplicates}>
						{duplicatesLoading ? 'Checking exact duplicates…' : 'Find exact duplicates'}
					</button>
					{#if duplicatesError}<p class="error" role="alert">{duplicatesError}</p>{/if}
					{#if duplicates}
						{#if !duplicates.groups.length}<p role="status">No exact duplicates found.</p>{/if}
						{#if duplicates.unavailable}<p class="muted">
								{duplicates.unavailable} unavailable or changed files were skipped.
							</p>{/if}
						{#each duplicates.groups as group (group.byte_size)}
							<p>{group.count} identical files ({group.byte_size} bytes). Showing up to 200.</p>
							{#each group.items.filter((entry) => entry.id !== item.id) as entry (entry.id)}
								<div class="row duplicate">
									<button type="button" class="btn btn-ghost" onclick={() => select(entry)}
										>{entry.filename}</button
									>
									<button
										type="button"
										class="btn"
										aria-label={`Delete duplicate ${entry.filename}`}
										onclick={() => deleteDuplicate(entry)}>Delete</button
									>
								</div>
							{/each}
						{/each}
					{/if}
					<label class="row">
						<input
							type="checkbox"
							bind:checked={vertical}
							onchange={() => writeFlag('simpleui.viewerVertical', vertical)}
						/>
						Swipe up/down to browse
					</label>
					{#if gallery.detailLoading}
						<p class="muted">Loading generation…</p>
					{:else if gallery.detailError}
						<p class="error" role="alert">{gallery.detailError}</p>
					{:else if !item.generation_id && gallery.provenance}
						<p>Imported provenance: {gallery.provenance.source}</p>
						{#if Object.keys(gallery.provenance.values).length}
							<MetaValues values={gallery.provenance.values} />
						{/if}
						{#each gallery.provenance.diagnostics as diagnostic (diagnostic)}
							<p class="muted">{diagnostic}</p>
						{/each}
						<details>
							<summary>Raw embedded metadata</summary>
							<pre>{JSON.stringify(gallery.provenance.raw, null, 2)}</pre>
						</details>
					{:else if gallery.detail}
						{@const detail = gallery.detail}
						<GenerationSummary {detail} />
						{#if detail.effective_values}
							<details>
								<summary>Saved prompt and input values</summary>
								<MetaValues values={detail.effective_values} />
							</details>
							{#if detail.workflow_id}
								<a
									class="btn btn-primary"
									href={`/generation/${detail.workflow_id}?reuse=${detail.id}`}>Reuse as draft</a
								>
							{/if}
						{:else}
							<p class="muted">
								Saved prompt and workflow inputs are unavailable. The media and generation status
								remain.
							</p>
							{#if detail.workflow_id}
								<a class="btn" href={`/generation/${detail.workflow_id}`}>Open workflow</a>
							{/if}
						{/if}
						{#key detail.id}
							<GraphJson generationId={detail.id} />
						{/key}
					{/if}
				</aside>
			{/if}
		</div>
	</dialog>
{/if}

<style>
	dialog {
		position: fixed;
		inset: 0;
		width: 100%;
		max-width: none;
		height: 100dvh;
		max-height: none;
		margin: 0;
		padding: 0;
		border: 0;
		color: #fff;
		color-scheme: dark;
		background: rgb(0 0 0 / 0.9);
		backdrop-filter: blur(8px);
		overflow: hidden;
	}
	dialog[open] {
		display: flex;
		flex-direction: column;
	}

	.body:fullscreen {
		width: 100%;
		height: 100dvh;
		background: #090909;
		color: #fff;
	}
	.playback-error {
		position: absolute;
		top: 6rem;
		z-index: 3;
		background: #090909;
	}
	.duplicate {
		flex-wrap: wrap;
		overflow-wrap: anywhere;
	}
	.view {
		position: relative;
		flex: 1 1 0;
		min-width: 0;
		min-height: 0;
		display: flex;
	}
	/* Controls float over the image; only the buttons take pointer events. */
	header {
		position: absolute;
		inset: 0 0 auto;
		z-index: 2;
		pointer-events: none;
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-1);
		padding: var(--space-1) max(var(--space-2), env(safe-area-inset-right)) var(--space-1)
			max(var(--space-3), env(safe-area-inset-left));
		padding-top: max(var(--space-1), env(safe-area-inset-top));
		text-shadow: 0 1px 3px #000;
	}
	/* No scrim over the media: each control carries its own chip instead. */
	header :is(button, a, select),
	.fold {
		pointer-events: auto;
		background: rgb(0 0 0 / 0.5);
		backdrop-filter: blur(4px);
	}
	header .btn-ghost {
		color: #fff;
	}
	/* White icon on a translucent chip (save, full size). */
	.ctl {
		color: #fff;
		background: rgb(255 255 255 / 0.14);
		border-color: transparent;
	}
	.ctl:hover,
	.ctl[aria-pressed='true'] {
		background: rgb(255 255 255 / 0.28);
	}
	h2 {
		margin: 0;
		font-size: var(--text-base);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.meta {
		margin: 0;
		font-size: var(--text-xs);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.fav {
		color: var(--color-accent);
	}

	.body {
		flex: 1 1 auto;
		min-height: 0;
		display: flex;
		flex-direction: column;
	}

	.stage {
		position: relative;
		flex: 1 1 0;
		min-height: 0;
		min-width: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0;
		touch-action: pan-y;
		overflow: hidden;
	}
	.media-frame {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		translate: calc(var(--slide-offset) * 100%) 0;
		opacity: 0;
		pointer-events: none;
		transition:
			translate 260ms var(--ease),
			opacity 220ms var(--ease);
	}
	.stage.vertical .media-frame {
		translate: 0 calc(var(--slide-offset) * 100%);
	}
	.media-frame.current {
		opacity: 1;
		pointer-events: auto;
	}
	.image-loading {
		position: absolute;
		bottom: 6rem;
		padding: var(--space-2);
		border-radius: var(--radius);
		background: rgb(0 0 0 / 0.5);
	}
	.stage img,
	.stage video {
		max-width: 100%;
		max-height: 100%;
		object-fit: contain;
		user-select: none;
	}
	/* Vertical browsing owns up/down drags, so the browser must not claim them as scrolls. */
	.stage.vertical:not(.full) {
		touch-action: none;
	}
	.stage.full {
		display: flex;
		overflow: hidden;
		touch-action: none;
		cursor: grab;
	}
	.stage.full img {
		max-width: none;
		max-height: none;
		flex: none;
		will-change: transform;
	}
	.missing {
		color: var(--color-danger);
		text-align: center;
	}

	.strip-wrap {
		position: absolute;
		inset: auto 0 0;
		z-index: 2;
		display: flex;
		flex-direction: column;
		align-items: center;
		pointer-events: none;
	}
	.strip-wrap > * {
		pointer-events: auto;
	}
	.fold {
		min-height: 1.75rem;
		width: 2.5rem;
		min-width: 0;
		padding: 0;
		border-radius: var(--radius);
	}
	.strip {
		overscroll-behavior: contain;
		width: 100%;
		box-sizing: border-box;
		display: flex;
		gap: var(--space-2);
		overflow-x: auto;
		padding: var(--space-2) var(--space-3);
		padding-bottom: max(var(--space-2), env(safe-area-inset-bottom));
	}
	.thumb {
		flex: none;
		display: grid;
		place-items: center;
		width: 4rem;
		height: 4rem;
		padding: 0;
		color: var(--color-text-muted);
		background: var(--color-surface-2);
		border: 2px solid transparent;
		border-radius: var(--radius);
		overflow: hidden;
		cursor: pointer;
	}
	.thumb[aria-current='true'] {
		border-color: var(--color-accent);
	}

	.nav {
		position: absolute;
		top: 50%;
		transform: translateY(-50%);
		z-index: 1;
		border-radius: 50%;
		background: var(--color-overlay);
		color: var(--color-text);
		border-color: transparent;
		backdrop-filter: blur(4px);
	}
	.nav:active:not(:disabled) {
		transform: translateY(-50%);
	}
	.prev {
		left: var(--space-2);
	}
	.next {
		right: var(--space-2);
	}

	.details {
		overscroll-behavior: contain;
		flex: none;
		max-height: 45%;
		overflow-y: auto;
		scrollbar-gutter: stable;
		padding: var(--space-3);
		padding-bottom: max(var(--space-3), env(safe-area-inset-bottom));
		background: var(--color-surface-1);
		border-top: 1px solid var(--color-border);
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.details > * {
		margin: 0;
	}
	.details .btn {
		align-self: flex-start;
	}

	.details summary {
		cursor: pointer;
		min-height: var(--control-h);
		display: flex;
		align-items: center;
	}
	.error {
		color: var(--color-danger);
		overflow-wrap: anywhere;
	}
	.body > :global(.divider) {
		display: none;
	}

	@media (min-width: 768px) {
		.body.with-details {
			flex-direction: row;
		}
		.body > :global(.divider) {
			display: block;
			flex: none;
			width: 6px;
			cursor: col-resize;
			touch-action: none;
			background: linear-gradient(var(--color-border), var(--color-border)) center / 2px 100%
				no-repeat;
		}
		.body > :global(.divider):hover,
		.body > :global(.divider):focus-visible {
			background-image: linear-gradient(var(--color-accent), var(--color-accent));
			outline: none;
		}
		.details {
			width: 22rem;
			max-height: none;
			border-top: 0;
			border-left: 0;
		}
	}
</style>
