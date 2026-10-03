<script lang="ts">
	import type { ControlDescriptor, EditValue } from '$lib/contracts';
	import { DIMENSION_PRESETS, dimensionPair } from './aspect';
	let { width, height, widthValue, heightValue, presets, onchange }: {
		width: ControlDescriptor; height: ControlDescriptor;
		widthValue: EditValue; heightValue: EditValue;
		presets?: [number, number][] | null;
		onchange: (width: string, height: string) => void;
	} = $props();
	const w = $derived(Number(widthValue)), h = $derived(Number(heightValue));
	const pixels = $derived(Number.isFinite(w * h) && w > 0 && h > 0 ? (w * h / 1_000_000).toFixed(2) : null);
	const swapped = $derived(dimensionPair(width, height, String(heightValue), String(widthValue)));
	function apply(pair: [string, string] | null): void { if (pair) onchange(...pair); }
</script>

<div class="stack" role="group" aria-label="Aspect ratio">
	<div class="row wrap">
		{#each presets ?? DIMENSION_PRESETS as [pw, ph], i (i)}
			{@const pair = dimensionPair(width, height, pw, ph)}
			<button type="button" class="btn" disabled={!pair} aria-pressed={pair?.[0] === String(widthValue) && pair?.[1] === String(heightValue)} onclick={() => apply(pair)}>{pw} × {ph}</button>
		{/each}
		<button type="button" class="btn" disabled={!swapped} onclick={() => apply(swapped)}>Swap orientation</button>
	</div>
	<p class="muted small">{widthValue} × {heightValue}{#if pixels} · {pixels} MP{/if}</p>
</div>

<style>.small { font-size: var(--text-xs); margin: 0; } button[aria-pressed='true'] { border-color: var(--color-accent); }</style>
