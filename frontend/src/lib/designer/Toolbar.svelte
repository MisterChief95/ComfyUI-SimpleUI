<script lang="ts">
	// Designer toolbar: back, title, Library (when it is a sheet), undo/redo,
	// preview width, reset to automatic, open the run page, Save. On phones it
	// wraps into two rows (title + Save, then the tools).
	import Icon from '$lib/ui/Icon.svelte';
	import { PREVIEW_PX, type Designer, type PreviewWidth } from './editor.svelte';

	let {
		editor,
		phone,
		stacked,
		showLibrary,
		onlibrary,
		oncopy
	}: {
		editor: Designer;
		phone: boolean;
		/** Narrower than the three-column desktop: title + Save on one row, tools on the next. */
		stacked: boolean;
		/** The library lives in a sheet or drawer: show the button that opens it. */
		showLibrary: boolean;
		onlibrary: () => void;
		/** Open "Copy layout from…". */
		oncopy: () => void;
	} = $props();

	const WIDTHS: { id: PreviewWidth; label: string }[] = [
		{ id: 'phone', label: 'Phone' },
		{ id: 'tablet', label: 'Tablet' },
		{ id: 'full', label: 'Full' }
	];

	function reset(): void {
		if (
			confirm(
				'Reset to the automatic layout? The saved layout is deleted from the server. You can still undo it here and save again.'
			)
		) {
			void editor.resetToAutomatic();
		}
	}
</script>

{#snippet save()}
	<button
		type="button"
		class="btn btn-primary save"
		disabled={!editor.dirty || editor.saving}
		aria-keyshortcuts="Control+S Meta+S"
		onclick={() => editor.save()}
	>
		<Icon name="save" size={18} />
		<span>{editor.saving ? 'Saving…' : 'Save'}</span>
		{#if editor.dirty && !editor.saving}<span class="dot" role="img" aria-label="Unsaved changes"
			></span>{/if}
	</button>
{/snippet}

<div class="toolbar">
	<div class="head">
		<a
			class="btn btn-ghost btn-icon"
			href="/workflows"
			aria-label="Back to workflows"
			title="Workflows"
		>
			<Icon name="chevron-left" />
		</a>
		<h1 title={editor.name}>{editor.name || 'Designer'}</h1>
		{#if stacked}{@render save()}{/if}
	</div>

	<div class="tools">
		{#if showLibrary}
			<button type="button" class="btn" onclick={onlibrary}>
				<Icon name="plus" size={18} /> <span>Library</span>
			</button>
		{/if}
		<button
			type="button"
			class="btn btn-ghost btn-icon"
			aria-label="Undo"
			title="Undo (Ctrl+Z)"
			disabled={!editor.history.canUndo}
			onclick={() => editor.undo()}
		>
			<Icon name="undo" size={18} />
		</button>
		<button
			type="button"
			class="btn btn-ghost btn-icon"
			aria-label="Redo"
			title="Redo (Ctrl+Shift+Z)"
			disabled={!editor.history.canRedo}
			onclick={() => editor.redo()}
		>
			<Icon name="redo" size={18} />
		</button>
		{#if !phone}
			<div class="seg" role="group" aria-label="Preview width">
				{#each WIDTHS as w (w.id)}
					<button
						type="button"
						aria-pressed={editor.previewWidth === w.id}
						title={PREVIEW_PX[w.id] ? `${PREVIEW_PX[w.id]}px` : 'Full width'}
						onclick={() => (editor.previewWidth = w.id)}
					>
						{w.label}
					</button>
				{/each}
			</div>
		{/if}
		<span class="spacer"></span>
		<button
			type="button"
			class="btn btn-ghost"
			disabled={editor.busy || !editor.schema}
			aria-label="Copy layout from another workflow"
			title="Copy layout from another workflow"
			onclick={oncopy}
		>
			<Icon name="copy" size={18} /> <span class="long">Copy layout from…</span>
		</button>
		<button
			type="button"
			class="btn btn-ghost"
			disabled={editor.busy || !editor.schema}
			aria-label="Reset to automatic layout"
			title="Reset to automatic layout"
			onclick={reset}
		>
			<Icon name="reset" size={18} /> <span class="long">Reset to automatic</span>
		</button>
		<a
			class="btn btn-ghost"
			href={`/generation/${editor.workflowId}`}
			aria-label="Open run page"
			title="Open run page"
		>
			<Icon name="external" size={18} /> <span class="long">Run page</span>
		</a>
		{#if !stacked}{@render save()}{/if}
	</div>
</div>

<style>
	.toolbar {
		flex: none;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-1) var(--space-2);
		padding: var(--space-1) var(--space-2);
		background: var(--color-surface-1);
		border-bottom: 1px solid var(--color-border);
	}
	.head {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		flex: 1 1 100%;
		min-width: 0;
	}
	h1 {
		flex: 1 1 auto;
		min-width: 0;
		margin: 0;
		font-size: var(--text-lg);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.tools {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		flex: 1 1 100%;
		min-width: 0;
	}
	.spacer {
		flex: 1 1 0;
	}
	.long {
		display: none;
	}
	.save {
		position: relative;
	}
	.dot {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
		background: var(--color-accent-text);
	}

	.seg {
		display: inline-flex;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		overflow: hidden;
	}
	.seg button {
		min-height: var(--control-h);
		padding: 0 0.75rem;
		font-weight: 600;
		color: var(--color-text-muted);
		background: var(--color-surface-2);
		border: 0;
		border-right: 1px solid var(--color-border);
		cursor: pointer;
	}
	.seg button:last-child {
		border-right: 0;
	}
	.seg button[aria-pressed='true'] {
		color: var(--color-accent-text);
		background: var(--color-accent);
	}

	@media (min-width: 1200px) {
		.head {
			flex: 0 1 18rem;
		}
		.tools {
			flex: 1 1 0;
		}
	}
	@media (min-width: 700px) {
		.long {
			display: inline;
		}
	}
</style>
