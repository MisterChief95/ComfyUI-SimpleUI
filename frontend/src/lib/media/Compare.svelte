<script lang="ts">
	// Compare two gallery items: side by side (stacked on phones) or an image swipe slider,
	// plus a diff of the two generations' saved effective values.
	import { api, describeApiError } from '$lib/api';
	import type { GenerationDetail, MediaInfo } from '$lib/contracts';
	import Sheet from '$lib/ui/Sheet.svelte';
	import { canReuseBoth, diffValues } from './compare';

	let { items, onclose }: { items: [MediaInfo, MediaInfo]; onclose: () => void } = $props();

	let open = $state(true);
	let swipe = $state(false);
	let split = $state(50);
	let details = $state<(GenerationDetail | null)[]>([null, null]);
	let error = $state<string | null>(null);
	let showSame = $state(false);

	const canSwipe = $derived(items.every((i) => i.media_kind === 'image'));
	const rows = $derived(diffValues(details[0]?.effective_values ?? null, details[1]?.effective_values ?? null));

	const changed = $derived(rows.filter((r) => !r.same).length);
	const shown = $derived(showSame ? rows : rows.filter((r) => !r.same));
	const reusable = $derived(canReuseBoth(details[0], details[1]));

	$effect(() => {
		const targets = items;
		error = null;
		Promise.all(targets.map((i) => (i.generation_id ? api<GenerationDetail>(`/generations/${i.generation_id}`) : null)))
			.then((d) => { if (items === targets) details = d; })
			.catch((cause) => { error = describeApiError(cause); });
	});
</script>

<Sheet bind:open title="Compare" variant="drawer" {onclose}>
	<div class="stack" style:--gap="var(--space-2)">
		{#if canSwipe}
			<label class="row"><input type="checkbox" bind:checked={swipe} /> Swipe slider</label>
		{/if}
		{#if swipe && canSwipe}
			<div class="swipe">
				<img src={`/api/media/${items[1].id}/file`} alt={items[1].filename} />
				<img class="top" style:clip-path={`inset(0 ${100 - split}% 0 0)`} src={`/api/media/${items[0].id}/file`} alt={items[0].filename} />
			</div>
			<input type="range" min="0" max="100" bind:value={split} aria-label="Swipe position" />
		{:else}
			<div class="pair">
				{#each items as item (item.id)}
					{#if item.media_kind === 'video'}
						<!-- svelte-ignore a11y_media_has_caption -->
						<video controls muted playsinline preload="metadata" src={`/api/media/${item.id}/file`}></video>
					{:else}
						<img src={`/api/media/${item.id}/file`} alt={item.filename} />
					{/if}
				{/each}
			</div>
		{/if}
		{#if error}<p class="error" role="alert">{error}</p>{/if}
		{#if reusable && details[0] && details[1]}
			<div class="row">
				<a class="btn grow" href={`/generation/${details[0].workflow_id}?reuse=${details[0].id}`}>Reuse A</a>
				<a class="btn grow" href={`/generation/${details[1].workflow_id}?reuse=${details[1].id}`}>Reuse B</a>
			</div>
		{/if}
		{#if rows.length}
			<label class="row">
				<input type="checkbox" bind:checked={showSame} />
				Show unchanged ({rows.length - changed}) · {changed} changed
			</label>
			<table>
				<thead><tr><th>Value</th><th>A</th><th>B</th></tr></thead>
				<tbody>
					{#each shown as row (row.key)}
						<tr class:diff={!row.same}><td>{row.key}</td><td>{row.a}</td><td>{row.b}</td></tr>
					{/each}
				</tbody>
			</table>
		{:else if !error}
			<p class="muted">No saved values to compare.</p>
		{/if}
	</div>
</Sheet>

<style>
	.pair { display: grid; grid-template-columns: 1fr; gap: var(--space-2); }
	@media (min-width: 768px) { .pair { grid-template-columns: 1fr 1fr; } }
	img, video { width: 100%; height: auto; max-height: 60dvh; object-fit: contain; }
	.swipe { position: relative; }
	.swipe .top { position: absolute; inset: 0; }
	table { width: 100%; border-collapse: collapse; font-size: var(--text-sm); }
	td, th { text-align: left; padding: var(--space-1); overflow-wrap: anywhere; border-bottom: 1px solid var(--color-border); }
	td:first-child, th:first-child { white-space: nowrap; width: 1%; }
	tr.diff td { background: color-mix(in srgb, var(--color-accent) 15%, transparent); }
	.error { color: var(--color-danger); }
</style>
