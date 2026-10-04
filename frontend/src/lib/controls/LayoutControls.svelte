<script lang="ts">
	// Shared control/pair rendering; draft state and preview policy stay with the caller.
	import type { AspectRatioLayoutItem, ControlDescriptor, EditValue } from '$lib/contracts';
	import AspectRatio from './AspectRatio.svelte';
	import ControlRow from './ControlRow.svelte';

	let {
		control,
		height,
		ratio,
		valueFor,
		onchange,
		isModified,
		onreset,
		lastSeed,
		workflowId,
		compact = false,
		preview = false
	}: {
		control: ControlDescriptor;
		height?: ControlDescriptor;
		ratio?: AspectRatioLayoutItem;
		valueFor: (control: ControlDescriptor) => EditValue;
		onchange: (control: ControlDescriptor, value: EditValue) => void;
		isModified?: (control: ControlDescriptor) => boolean;
		onreset?: (control: ControlDescriptor) => void;
		lastSeed?: (control: ControlDescriptor) => string | null;
		workflowId?: string;
		compact?: boolean;
		preview?: boolean;
	} = $props();
</script>

{#if ratio && height}
	<AspectRatio
		width={control}
		{height}
		presets={ratio.presets}
		widthValue={valueFor(control)}
		heightValue={valueFor(height)}
		onchange={(w, h) => {
			onchange(control, w);
			onchange(height, h);
		}}
	/>
{/if}
{#each height ? [control, height] : [control] as entry (entry.binding_id)}
	<ControlRow
		control={entry}
		value={valueFor(entry)}
		onchange={(value) => onchange(entry, value)}
		modified={isModified?.(entry) ?? false}
		onreset={onreset ? () => onreset?.(entry) : undefined}
		lastSeed={entry.component === 'seed' ? lastSeed?.(entry) : undefined}
		{workflowId}
		{compact}
		{preview}
	/>
{/each}
