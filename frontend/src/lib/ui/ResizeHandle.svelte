<script lang="ts">
	import { panelWidth, resizeKey } from './resize';
	import { writeStored } from './storage';

	let {
		width = $bindable(null),
		container,
		label,
		storageKey,
		min,
		remaining,
		defaultWidth,
		extremes = false,
		class: className = ''
	}: {
		width?: number | null;
		container: HTMLElement | undefined;
		label: string;
		storageKey: string;
		min: number;
		remaining: number;
		defaultWidth: number;
		extremes?: boolean;
		class?: string;
	} = $props();

	let dragging = false;
	let right = 0;
	function setWidth(px: number): void {
		width = panelWidth(px, container?.clientWidth ?? 0, min, remaining);
	}
	function persist(): void {
		if (width !== null) writeStored(storageKey, String(width));
	}
	function down(event: PointerEvent): void {
		if (!container) return;
		right = container.getBoundingClientRect().right;
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		dragging = true;
	}
	function end(): void {
		if (!dragging) return;
		dragging = false;
		persist();
	}
	function keydown(event: KeyboardEvent): void {
		const next = resizeKey(event.key, width ?? defaultWidth, event.shiftKey, extremes);
		if (next === null) return;
		event.preventDefault();
		setWidth(next);
		persist();
	}
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div
	class="resize-handle {className}"
	role="separator"
	aria-orientation="vertical"
	aria-label={label}
	aria-valuemin={min}
	aria-valuemax={Math.max(min, (container?.clientWidth ?? 0) - remaining)}
	aria-valuenow={width ?? defaultWidth}
	aria-valuetext={`${width ?? defaultWidth} pixels`}
	tabindex="0"
	onpointerdown={down}
	onpointermove={(event) => {
		if (dragging) setWidth(right - event.clientX);
	}}
	onpointerup={end}
	onpointercancel={end}
	onlostpointercapture={end}
	onkeydown={keydown}
></div>

<style>
	.resize-handle {
		cursor: col-resize;
		touch-action: none;
	}
</style>
