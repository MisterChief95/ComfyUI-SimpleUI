<script lang="ts">
	// Modal panel on a native <dialog> (focus trap, Escape, inert background).
	//   <Sheet bind:open title="Library" side="left">...</Sheet>
	// - phone (< 768px): bottom sheet, max 85dvh, body scrolls.
	// - wider, or variant="drawer": side drawer on `side` ("left" | "right").
	// - variant="sheet" keeps the bottom sheet at every width.
	// - half: the bottom sheet rests at half height over a light backdrop, so the
	//   content it edits stays visible above it; an Expand button grows it.
	// Clicking the backdrop, pressing Escape or the close button sets open=false
	// and calls onclose. `footer` is pinned under the scrolling body.
	import type { Snippet } from 'svelte';
	import { on } from 'svelte/events';
	import { MediaQuery } from 'svelte/reactivity';
	import Icon from './Icon.svelte';
	import { isolateInput } from './isolateInput';

	let {
		open = $bindable(false),
		title,
		side = 'right',
		variant = 'auto',
		half = false,
		onclose,
		children,
		footer
	}: {
		open?: boolean;
		title: string;
		side?: 'left' | 'right';
		variant?: 'auto' | 'sheet' | 'drawer';
		half?: boolean;
		onclose?: () => void;
		children: Snippet;
		footer?: Snippet;
	} = $props();

	const wide = new MediaQuery('min-width: 768px');
	const drawer = $derived(variant === 'drawer' || (variant === 'auto' && wide.current));

	let dialog = $state<HTMLDialogElement>();
	let expanded = $state(false);
	const resting = $derived(half && !drawer && !expanded);

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		else if (!open && dialog.open) dialog.close();
	});

	function closed(): void {
		// Fires for Escape and dialog.close() alike.
		open = false;
		expanded = false;
		onclose?.();
	}

	function backdrop(event: MouseEvent): void {
		// Only the dialog element itself is hit when clicking its ::backdrop.
		if (event.target === dialog) dialog?.close();
	}

	const titleId = $props.id();
</script>

<dialog
	{@attach (element) => on(element, 'click', backdrop)}
	{@attach isolateInput}
	bind:this={dialog}
	class:drawer
	class:left={drawer && side === 'left'}
	class:half={resting}
	aria-labelledby={titleId}
	onclose={closed}
>
	<header>
		<h2 id={titleId}>{title}</h2>
		{#if half && !drawer}
			<button
				type="button"
				class="btn btn-ghost expand"
				aria-pressed={expanded}
				onclick={() => (expanded = !expanded)}
			>
				{expanded ? 'Shrink' : 'Expand'}
			</button>
		{/if}
		<button
			type="button"
			class="btn btn-ghost btn-icon"
			aria-label="Close"
			onclick={() => dialog?.close()}
		>
			<Icon name="close" />
		</button>
	</header>
	<div class="body">{@render children()}</div>
	{#if footer}<footer>{@render footer()}</footer>{/if}
</dialog>

<style>
	dialog {
		/* Bottom sheet (default). */
		position: fixed;
		inset: auto 0 0 0;
		width: 100%;
		max-width: none;
		max-height: 85dvh;
		margin: 0;
		padding: 0;
		color: var(--color-text);
		background: var(--color-surface-1);
		border: 1px solid var(--color-border);
		border-bottom: 0;
		border-radius: var(--radius-lg) var(--radius-lg) 0 0;
		box-shadow: var(--shadow-3);
		overflow: hidden;
	}
	dialog[open] {
		display: flex;
		flex-direction: column;
		animation: rise 0.22s var(--ease);
	}
	dialog::backdrop {
		background: var(--color-overlay);
	}
	dialog.half {
		max-height: 50dvh;
	}
	dialog.half::backdrop {
		background: transparent;
	}

	dialog.drawer {
		inset: 0 0 0 auto;
		width: min(26rem, 92vw);
		height: 100dvh;
		max-height: none;
		border: 0;
		border-left: 1px solid var(--color-border);
		border-radius: 0;
	}
	dialog.drawer.left {
		inset: 0 auto 0 0;
		border-left: 0;
		border-right: 1px solid var(--color-border);
	}
	dialog.drawer[open] {
		animation: slide-right 0.22s var(--ease);
	}
	dialog.drawer.left[open] {
		animation: slide-left 0.22s var(--ease);
	}

	header {
		flex: none;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		padding: var(--space-1) var(--space-2) var(--space-1) var(--space-3);
		border-bottom: 1px solid var(--color-border);
	}
	h2 {
		margin: 0;
		font-size: var(--text-lg);
	}
	.expand {
		margin-left: auto;
	}

	.body {
		flex: 1 1 auto;
		min-height: 0;
		overflow-y: auto;
		scrollbar-gutter: stable;
		overscroll-behavior: contain;
		padding: var(--space-3);
		/* Bottom sheet clears the home indicator; drawers the side notches. */
		padding-bottom: max(var(--space-3), env(safe-area-inset-bottom));
	}
	.drawer .body {
		padding-right: max(var(--space-3), env(safe-area-inset-right));
	}
	.drawer.left .body {
		padding-right: var(--space-3);
		padding-left: max(var(--space-3), env(safe-area-inset-left));
	}
	footer {
		flex: none;
		padding: var(--space-2) var(--space-3) max(var(--space-2), env(safe-area-inset-bottom));
		border-top: 1px solid var(--color-border);
	}

	@keyframes rise {
		from {
			transform: translateY(100%);
		}
	}
	@keyframes slide-right {
		from {
			transform: translateX(100%);
		}
	}
	@keyframes slide-left {
		from {
			transform: translateX(-100%);
		}
	}
</style>
