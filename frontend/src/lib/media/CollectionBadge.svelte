<script lang="ts">
	import { onDestroy } from 'svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import { isolatePopoverInput } from '$lib/ui/isolateInput';

	let { collections }: { collections: { id: string; name: string }[] } = $props();
	const id = $props.id();
	let button: HTMLButtonElement;
	let popup: HTMLDivElement;
	let opened = $state(false);
	let pinned = false;
	let held = false;
	let hold: ReturnType<typeof setTimeout> | undefined;
	let left = $state(0);
	let top = $state(0);
	const names = $derived(collections.map((collection) => collection.name).join(', '));

	function show(): void {
		const rect = button.getBoundingClientRect();
		left = Math.max(8, Math.min(rect.left, window.innerWidth - 268));
		top = Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 200));
		popup.showPopover();
	}
	function hide(): void {
		if (!pinned) popup.hidePopover();
	}
	function cancelHold(): void {
		clearTimeout(hold);
	}
	function press(event: PointerEvent): void {
		if (event.pointerType === 'mouse') return;
		held = false;
		cancelHold();
		hold = setTimeout(() => {
			held = true;
			pinned = true;
			show();
		}, 450);
	}
	function toggle(): void {
		if (held) {
			held = false;
			return;
		}
		pinned = !pinned;
		if (pinned) show();
		else popup.hidePopover();
	}
	onDestroy(cancelHold);
</script>

<button
	type="button"
	class="collection-badge"
	bind:this={button}
	aria-label={`Collections: ${names}`}
	aria-expanded={opened}
	aria-controls={id}
	popovertarget={id}
	onpointerenter={(event) => {
		if (event.pointerType === 'mouse') show();
	}}
	onpointerleave={() => {
		cancelHold();
		hide();
	}}
	onfocus={() => {
		if (button.matches(':focus-visible')) show();
	}}
	onblur={hide}
	onpointerdown={press}
	onpointerup={cancelHold}
	onpointercancel={cancelHold}
	oncontextmenu={(event) => event.preventDefault()}
	onclick={(event) => {
		// Pinning and long-press release use our toggle, rather than the native toggle action.
		event.preventDefault();
		toggle();
	}}
>
	<Icon name="bookmark" size={15} />
	{#if collections.length > 1}<span>{collections.length}</span>{/if}
</button>
<div
	{id}
	class="collection-names"
	popover="auto"
	bind:this={popup}
	style:left={`${left}px`}
	style:top={`${top}px`}
	{@attach opened ? isolatePopoverInput : undefined}
	ontoggle={(event) => {
		opened = event.newState === 'open';
		if (!opened) pinned = false;
	}}
>
	<strong>Collections</strong>
	<ul>
		{#each collections as collection (collection.id)}<li>{collection.name}</li>{/each}
	</ul>
</div>

<style>
	.collection-badge {
		position: absolute;
		left: 0.3rem;
		top: 0.3rem;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.2rem;
		min-width: 32px;
		min-height: 32px;
		padding: 0.3rem;
		border: 1px solid rgb(255 255 255 / 0.3);
		border-radius: var(--radius);
		background: rgb(0 0 0 / 0.65);
		color: white;
		font-size: var(--text-xs);
		cursor: pointer;
		touch-action: manipulation;
		-webkit-touch-callout: none;
	}
	.collection-names {
		position: fixed;
		inset: auto;
		margin: 0;
		width: min(260px, calc(100vw - 16px));
		max-height: min(180px, 45dvh);
		overflow: auto;
		padding: var(--space-2);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-surface-1);
		color: var(--color-text);
		box-shadow: var(--shadow-3);
		font-size: var(--text-sm);
		overflow-wrap: anywhere;
	}
	ul {
		margin: var(--space-1) 0 0;
		padding-left: 1.2rem;
	}
</style>
