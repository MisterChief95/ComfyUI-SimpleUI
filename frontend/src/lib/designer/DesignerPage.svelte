<script lang="ts">
	// Layout designer (docs/UI_DESIGNER.md "Designer"). All state lives in
	// lib/designer/editor.svelte.ts; this page wires the panels per breakpoint:
	//   desktop >= 1200  library | canvas | inspector
	//   tablet  >= 768   canvas | inspector, library in a left drawer
	//   phone            canvas only, library and inspector as bottom sheets
	import { onMount } from 'svelte';
	import { beforeNavigate } from '$app/navigation';
	import { MediaQuery } from 'svelte/reactivity';
	import Canvas from '$lib/designer/Canvas.svelte';
	import CopyLayout from '$lib/designer/CopyLayout.svelte';
	import { Dnd } from '$lib/designer/dnd.svelte';
	import { Designer } from '$lib/designer/editor.svelte';
	import Inspector from '$lib/designer/Inspector.svelte';
	import Library from '$lib/designer/Library.svelte';
	import Toolbar from '$lib/designer/Toolbar.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	let { workflowId }: { workflowId: string } = $props();
	// The route's keyed block creates a fresh designer for each workflow id.
	// svelte-ignore state_referenced_locally
	const editor = new Designer(workflowId);

	const dnd = new Dnd((payload, target) => {
		if (payload.type === 'section' && target.type === 'sections') {
			editor.reorderSection(payload.id, target.index);
		} else if (payload.type !== 'section' && target.type === 'items') {
			editor.placeAt(payload.id, target.sectionId, target.index, target.panel);
		}
	});

	const desktop = new MediaQuery('min-width: 1200px');
	const tablet = new MediaQuery('min-width: 768px');
	const phone = $derived(!tablet.current);

	let libraryOpen = $state(false);
	let inspectorOpen = $state(false);
	let copyOpen = $state(false);

	onMount(() => {
		void editor.load();
	});

	// Unsaved-changes guard: in-app navigation asks; leaving the page uses the
	// browser's own prompt (beforeunload below).
	beforeNavigate(({ cancel, willUnload }) => {
		if (!editor.dirty || willUnload) return;
		if (!confirm('You have unsaved changes. Leave without saving?')) cancel();
	});

	function onbeforeunload(event: BeforeUnloadEvent): void {
		if (editor.dirty) event.preventDefault();
	}

	// Ctrl/Cmd+S always saves; undo/redo defer to native text undo while typing.
	function onkeydown(event: KeyboardEvent): void {
		if (event.defaultPrevented || document.querySelector('dialog[open], :popover-open')) return;
		if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
		const key = event.key.toLowerCase();
		if (key === 's') {
			event.preventDefault();
			void editor.save();
			return;
		}
		const typing = (event.target as HTMLElement | null)?.closest(
			'textarea, [contenteditable], input:not([type="checkbox"], [type="radio"], [type="range"], [type="button"])'
		);
		if (typing) return;
		if (key === 'z' && !event.shiftKey) {
			event.preventDefault();
			editor.undo();
		} else if ((key === 'z' && event.shiftKey) || key === 'y') {
			event.preventDefault();
			editor.redo();
		}
	}

	// Phone: the inspector rests at half height, so lift the selected control
	// to the top of the canvas where it stays visible while being edited.
	function openInspector(): void {
		inspectorOpen = true;
		const id = editor.selection?.kind === 'control' ? editor.selection.id : null;
		if (id)
			document.querySelector(`[data-item="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'start' });
	}

	async function reload(): Promise<void> {
		if (editor.dirty && !confirm('Discard your changes and reload the saved version?')) return;
		await editor.load();
	}
</script>

<svelte:window {onkeydown} {onbeforeunload} />
<svelte:head><title>{editor.name ? `${editor.name} · ` : ''}Designer</title></svelte:head>

<div class="page-full designer">
	<Toolbar
		{editor}
		{phone}
		stacked={!desktop.current}
		showLibrary={!desktop.current}
		onlibrary={() => (libraryOpen = true)}
		oncopy={() => (copyOpen = true)}
	/>

	{#if editor.info}
		<div class="notice info" role="status">
			<p class="notice-body">{editor.info}</p>
			<button type="button" class="btn btn-ghost" onclick={() => (editor.info = null)}
				>Dismiss</button
			>
		</div>
	{/if}

	{#if editor.failures.length > 0}
		<div class="notice" role="alert">
			<div class="notice-body">
				<strong>{editor.conflicts ? 'Changed elsewhere' : 'Some changes were not saved'}</strong>
				<ul>
					{#each editor.failures as failure (failure.key)}
						<li><b>{failure.label}</b>: {failure.message}</li>
					{/each}
				</ul>
				{#if editor.conflicts}
					<p><b>Saved elsewhere since you opened this page:</b></p>
					{#if editor.conflictSnapshot}
						<ul>
							{#each editor.conflictChanges as change, index (index)}<li>{change}</li>{:else}<li>
									No section or item changes; the saved revision changed.
								</li>{/each}
						</ul>
					{:else}
						<p>Loading changes…</p>
						<button type="button" class="btn" onclick={() => editor.inspectConflict()}
							>Refresh changes</button
						>
					{/if}
				{/if}
			</div>
			<div class="row wrap">
				{#if editor.conflicts}
					<button type="button" class="btn" onclick={reload}>Reload (discard mine)</button>
					<button
						type="button"
						class="btn btn-primary"
						disabled={!editor.conflictSnapshot || editor.saving || editor.busy}
						onclick={() => editor.overwrite()}
					>
						Keep mine and overwrite
					</button>
				{/if}
				<button type="button" class="btn btn-ghost" onclick={() => editor.dismissFailures()}>
					Dismiss
				</button>
			</div>
		</div>
	{/if}

	{#if editor.loading}
		<p class="status muted">Loading workflow…</p>
	{:else if editor.loadError}
		<div class="status">
			<p class="error" role="alert">{editor.loadError}</p>
			<button type="button" class="btn" onclick={() => editor.load()}>Try again</button>
		</div>
	{:else}
		<div
			class="body"
			class:desktop={desktop.current}
			class:tablet={tablet.current && !desktop.current}
		>
			{#if desktop.current}
				<aside class="panel left" aria-label="Control library">
					<Library {editor} {dnd} draggable />
				</aside>
			{/if}
			<main class="center">
				<Canvas {editor} {dnd} compact={phone} onedit={openInspector} />
			</main>
			{#if tablet.current}
				<aside class="panel right" aria-label="Inspector">
					<Inspector {editor} />
				</aside>
			{/if}
		</div>
	{/if}
</div>

{#if !desktop.current}
	<Sheet bind:open={libraryOpen} title="Library" side="left">
		<Library {editor} {dnd} />
	</Sheet>
{/if}
{#if copyOpen}
	<CopyLayout {editor} bind:open={copyOpen} />
{/if}
{#if phone}
	<Sheet bind:open={inspectorOpen} title="Inspector" variant="sheet" half>
		<Inspector {editor} />
	</Sheet>
{/if}

<div class="sr-only" role="status" aria-live="polite">{editor.announcement}</div>

{#if dnd.payload && dnd.pointer}
	<div class="ghost" style:left="{dnd.pointer.x + 12}px" style:top="{dnd.pointer.y + 12}px">
		{dnd.payload.label}
	</div>
	{#if dnd.bar}
		<div
			class="drop-bar"
			style:left="{dnd.bar.left}px"
			style:top="{dnd.bar.top}px"
			style:width="{dnd.bar.width}px"
			style:height="{dnd.bar.height}px"
		></div>
	{/if}
{/if}

<style>
	.designer {
		background: var(--color-bg);
	}
	.body {
		flex: 1 1 auto;
		min-height: 0;
		display: grid;
		grid-template-columns: minmax(0, 1fr);
	}
	.body.tablet {
		grid-template-columns: minmax(0, 1fr) 20rem;
	}
	.body.desktop {
		grid-template-columns: 18rem minmax(0, 1fr) 20rem;
	}
	.center {
		display: flex;
		flex-direction: column;
		min-width: 0;
		min-height: 0;
	}
	.panel {
		min-height: 0;
		overflow-y: auto;
		scrollbar-gutter: stable;
		overscroll-behavior: contain;
		padding: var(--space-3);
		background: var(--color-surface-1);
	}
	.panel.left {
		border-right: 1px solid var(--color-border);
	}
	.panel.right {
		border-left: 1px solid var(--color-border);
	}
	.status {
		padding: var(--space-4);
	}
	.error {
		color: var(--color-danger);
	}

	.notice {
		flex: none;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2) var(--space-3);
		padding: var(--space-2) var(--space-3);
		background: var(--color-danger-soft);
		border-bottom: 1px solid var(--color-danger);
		max-height: 30dvh;
		overflow-y: auto;
		scrollbar-gutter: stable;
	}
	.notice.info {
		background: var(--color-surface-2);
		border-bottom-color: var(--color-border);
	}
	.notice-body {
		min-width: 0;
		flex: 1 1 18rem;
		font-size: var(--text-sm);
	}
	.notice ul {
		margin: 0.25rem 0 0;
		padding-left: 1.1rem;
	}

	.ghost {
		position: fixed;
		z-index: 1000;
		max-width: 16rem;
		padding: 0.4rem 0.7rem;
		font-size: var(--text-sm);
		font-weight: 600;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		color: var(--color-accent-text);
		background: var(--color-accent);
		border-radius: var(--radius);
		box-shadow: var(--shadow-3);
		pointer-events: none;
		opacity: 0.92;
	}
	.drop-bar {
		position: fixed;
		z-index: 999;
		border-radius: 2px;
		background: var(--color-accent);
		box-shadow: 0 0 0 2px var(--color-accent-soft);
		pointer-events: none;
	}
</style>
