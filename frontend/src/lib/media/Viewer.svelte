<script lang="ts">
	// Full-screen media viewer on a native <dialog> (focus trap + Escape).
	// Mount it only while an item is selected: {#if gallery.selected}<Viewer {gallery} />{/if}
	// Navigation: prev/next buttons, ArrowLeft/ArrowRight, horizontal swipe on images,
	// and (opt-in, per device) vertical swipe: up = next, down = previous.
	import { onMount } from 'svelte';
	import { on } from 'svelte/events';
	import { MediaQuery } from 'svelte/reactivity';
	import Icon from '$lib/ui/Icon.svelte';
	import { isolateInput } from '$lib/ui/isolateInput';
	import { settingsState } from '$lib/settings.svelte';
	import GraphJson from './GraphJson.svelte';
	import MetaValues from './MetaValues.svelte';
	import Star from './Star.svelte';
	import { formatDate } from './format';
	import { workflowNames } from './workflowNames.svelte';
	import type { GalleryState } from './gallery.svelte';

	let { gallery }: { gallery: GalleryState } = $props();

	const wide = new MediaQuery('min-width: 768px');
	let dialog = $state<HTMLDialogElement>();
	// Details side panel (desktop) or bottom panel (phone). Closed on phone by default.
	let showDetails = $state(loadFlag('simpleui.viewerDetails', wide.current));
	let showStrip = $state(loadFlag('simpleui.viewerStrip', true));
	let vertical = $state(loadFlag('simpleui.viewerVertical', false));

	function loadFlag(key: string, fallback: boolean): boolean {
		try {
			const saved = localStorage.getItem(key);
			return saved === null ? fallback : saved === '1';
		} catch {
			return fallback;
		}
	}
	function saveFlag(key: string, value: boolean): void {
		try {
			localStorage.setItem(key, value ? '1' : '0');
		} catch {
			/* not persisted */
		}
	}

	// Desktop details width in px; null = 22rem. Per-device preference. Below 768px the panel is a bottom sheet and not resizable.
	const WIDTH_KEY = 'simpleui.viewerDetailsWidth';
	let body = $state<HTMLElement>();
	let detailsW = $state<number | null>(null);
	try {
		const saved = Number(localStorage.getItem(WIDTH_KEY));
		if (saved > 0) detailsW = saved;
	} catch {
		/* storage blocked: default width */
	}

	function setWidth(px: number, save: boolean): void {
		const total = body?.clientWidth ?? 0;
		// ponytail: fixed 240px / 320px-stage bounds; make them tokens if designs need other limits
		detailsW = Math.round(Math.min(Math.max(px, 240), Math.max(240, total - 320)));
		if (save)
			try {
				localStorage.setItem(WIDTH_KEY, String(detailsW));
			} catch {
				/* not persisted */
			}
	}
	function dragDivider(event: PointerEvent): void {
		const handle = event.currentTarget as HTMLElement;
		handle.setPointerCapture(event.pointerId);
		const right = body!.getBoundingClientRect().right;
		const move = (e: PointerEvent): void => setWidth(right - e.clientX, false);
		const up = (): void => {
			handle.removeEventListener('pointermove', move);
			handle.removeEventListener('pointerup', up);
			handle.removeEventListener('pointercancel', up);
			if (detailsW) setWidth(detailsW, true);
		};
		handle.addEventListener('pointermove', move);
		handle.addEventListener('pointerup', up);
		handle.addEventListener('pointercancel', up);
	}
	function keyDivider(event: KeyboardEvent): void {
		const step = event.shiftKey ? 64 : 16;
		const now = detailsW ?? 352;
		if (event.key === 'ArrowLeft') setWidth(now + step, true);
		else if (event.key === 'ArrowRight') setWidth(now - step, true);
		else return;
		event.preventDefault();
	}

	const item = $derived(gallery.selected);
	const index = $derived(gallery.selectedIndex);
	const hasPrev = $derived(index >= 0 && (index > 0 || gallery.canLoadPrevious));
	const hasNext = $derived(index >= 0 && (index < gallery.items.length - 1 || gallery.canLoadMore));

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
		if (event.key === 'ArrowLeft' && hasPrev) {
			event.preventDefault();
			gallery.step(-1);
		} else if (event.key === 'ArrowRight' && hasNext) {
			event.preventDefault();
			gallery.step(1);
		}
	}

	let swipe: { x: number; y: number } | null = null;

	let downAt = { x: 0, y: 0 };

	// A tap on the blank stage (not the image, buttons, or the end of a swipe/pan) closes the viewer.
	function stageClick(event: MouseEvent): void {
		if (event.target !== stage || full) return;
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
		swipe = full || onControls ? null : { x: event.clientX, y: event.clientY };
	}

	function pointerup(event: PointerEvent): void {
		if (!swipe) return;
		const dx = event.clientX - swipe.x;
		const dy = event.clientY - swipe.y;
		swipe = null;
		if (vertical && Math.abs(dy) >= 60 && Math.abs(dy) > Math.abs(dx) * 1.5) {
			if (dy < 0 && hasNext) gallery.step(1);
			else if (dy > 0 && hasPrev) gallery.step(-1);
			return;
		}
		if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
		if (dx > 0 && hasPrev) gallery.step(-1);
		else if (dx < 0 && hasNext) gallery.step(1);
	}

	const titleId = $props.id();
</script>

{#if item}
	<dialog
		{@attach (element) => on(element, 'keydown', onkeydown)}
		{@attach isolateInput}
		bind:this={dialog}
		aria-labelledby={titleId}
		onclose={() => gallery.close()}
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
						aria-expanded={showDetails}
						onclick={() => saveFlag('simpleui.viewerDetails', (showDetails = !showDetails))}
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
				{#if gallery.error}<p class="error" role="alert">{gallery.error}</p>{/if}
				{#if gallery.loading}<p class="muted" role="status">Loading media…</p>{/if}

				<div
					class="stage"
					class:full
					class:vertical
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
					onpointercancel={zoomUp}
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
							onclick={() => gallery.step(-1)}
						>
							<Icon name="chevron-left" size={24} />
						</button>
					{/if}
					{#key item.id}
						{#if gallery.isUnavailable(item)}
							<p class="missing" role="status">
								The file is unavailable. Its gallery and generation records are preserved.
							</p>
						{:else if item.media_kind === 'image'}
							<img
								src={`/api/media/${item.id}/file`}
								alt={item.filename}
								draggable="false"
								style:transform={full
									? `translate(${view.x}px, ${view.y}px) scale(${view.s})`
									: undefined}
								onerror={() => gallery.markUnavailable(item.id)}
							/>
						{:else if item.media_kind === 'video'}
							<video
								controls
								autoplay={settingsState.data?.profile.gallery_autoplay === true}
								muted={settingsState.data?.profile.gallery_autoplay === true}
								playsinline
								preload="metadata"
								src={`/api/media/${item.id}/file`}
								onerror={() => gallery.markUnavailable(item.id)}
							>
								<track kind="captions" />
							</video>
						{:else}
							<p class="muted">Preview is not available for this file type.</p>
						{/if}
					{/key}
					{#if hasNext && !full}
						<button
							type="button"
							class="nav next btn btn-icon"
							aria-label="Next"
							disabled={gallery.loading}
							onclick={() => gallery.step(1)}
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
							onclick={() => saveFlag('simpleui.viewerStrip', (showStrip = !showStrip))}
						>
							<Icon name={showStrip ? 'chevron-down' : 'chevron-up'} size={16} />
						</button>
						{#if showStrip}
							<div class="strip" role="group" aria-label="Items" bind:this={strip}>
								{#each gallery.items as entry (entry.id)}
									<button
										type="button"
										class="thumb"
										aria-label={entry.filename}
										aria-current={entry.id === item.id}
										onclick={() => gallery.select(entry)}
									>
										{#if entry.media_kind !== 'other' && entry.state !== 'unavailable' && !gallery.thumbnailMissing[entry.id]}
											<img
												src={`/api/media/${entry.id}/thumbnail`}
												alt=""
												loading="lazy"
												onerror={() => gallery.markThumbnailMissing(entry.id)}
											/>
										{:else}
											<Icon name={entry.media_kind === 'video' ? 'video' : 'image'} size={24} />
										{/if}
									</button>
								{/each}
							</div>
						{/if}
					</div>
				{/if}
			</div>

			{#if showDetails}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
				<div
					class="divider"
					role="separator"
					aria-orientation="vertical"
					aria-label="Resize details panel"
					tabindex="0"
					onpointerdown={dragDivider}
					onkeydown={keyDivider}
				></div>
				<aside
					class="details"
					aria-label="Generation details"
					style:width={wide.current && detailsW ? `${detailsW}px` : undefined}
				>
					<h3>Generation details</h3>
					<label class="row">
						<input
							type="checkbox"
							bind:checked={vertical}
							onchange={() => saveFlag('simpleui.viewerVertical', vertical)}
						/>
						Swipe up/down to browse
					</label>
					{#if !item.generation_id}
						<p class="muted">Workflow and prompt are unknown for this imported media.</p>
					{:else if gallery.detailLoading}
						<p class="muted">Loading generation…</p>
					{:else if gallery.detailError}
						<p class="error" role="alert">{gallery.detailError}</p>
					{:else if gallery.detail}
						{@const detail = gallery.detail}
						<div class="row wrap">
							<span
								class="badge"
								class:badge-success={detail.status === 'succeeded'}
								class:badge-danger={detail.status === 'failed'}>{detail.status}</span
							>
							<span class="badge">{detail.output_state}</span>
						</div>
						{#if detail.workflow_id}
							<p>
								Workflow: <strong
									>{workflowNames.name(detail.workflow_id) ?? detail.workflow_id}</strong
								>
							</p>
						{/if}
						{#if detail.error}
							<pre class="error">{JSON.stringify(detail.error, null, 2)}</pre>
						{/if}
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
		gap: var(--space-1);
		padding: var(--space-1) max(var(--space-2), env(safe-area-inset-right)) var(--space-1)
			max(var(--space-3), env(safe-area-inset-left));
		padding-top: max(var(--space-1), env(safe-area-inset-top));
		text-shadow: 0 1px 3px #000;
	}
	/* No scrim over the media: each control carries its own chip instead. */
	header :is(button, a),
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
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
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
	.details pre {
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font-size: var(--text-sm);
		font-family: var(--font-mono);
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
	.divider {
		display: none;
	}

	@media (min-width: 768px) {
		.body.with-details {
			flex-direction: row;
		}
		.divider {
			display: block;
			flex: none;
			width: 6px;
			cursor: col-resize;
			touch-action: none;
			background: linear-gradient(var(--color-border), var(--color-border)) center / 2px 100%
				no-repeat;
		}
		.divider:hover,
		.divider:focus-visible {
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
