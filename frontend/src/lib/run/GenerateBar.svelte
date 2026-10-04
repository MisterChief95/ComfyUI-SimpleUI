<script lang="ts">
	// Always-visible bar at the bottom of the run page (above the tab bar on a
	// phone, because the page is a flex column that ends here).
	import Icon from '$lib/ui/Icon.svelte';
	import { isTerminal } from './status';
	import type { RunState } from './run.svelte';

	let { run }: { run: RunState } = $props();

	const latest = $derived(run.tracker.latest);
	const cancellable = $derived(
		latest !== null && (latest.status === 'queued' || latest.status === 'running')
	);
	const message = $derived(
		run.submitError ??
			(cancellable ? run.cancelError : null) ??
			(run.blockingReason ? `Can't generate: ${run.blockingReason}` : null) ??
			(run.invalidReason ? `Can't generate: ${run.invalidReason}` : null)
	);
</script>

<div class="bar">
	{#if message}<p class="msg" role="alert">{message}</p>{/if}
	<div class="row actions">
		<span class="hint">Generate <kbd>Ctrl / ⌘ + Enter</kbd></span>
		{#if cancellable && latest && !isTerminal(latest.status)}
			<button
				type="button"
				class="btn btn-danger"
				disabled={run.cancelling}
				onclick={() => run.cancel()}
			>
				{run.cancelling ? 'Cancelling…' : 'Cancel'}
			</button>
		{/if}
		<button
			type="button"
			class="btn btn-primary generate"
			disabled={!run.canGenerate}
			title="Generate (Ctrl+Enter)"
			onclick={() => run.submit()}
		>
			<Icon name="generate" size={16} />
			{run.submitting ? 'Submitting…' : 'Generate'}
		</button>
	</div>
</div>

<style>
	.bar {
		flex: none;
		padding: var(--space-2) max(var(--page-pad), env(safe-area-inset-right)) var(--space-2)
			max(var(--page-pad), env(safe-area-inset-left));
		background: var(--color-surface-1);
		border-top: 1px solid var(--color-border);
		box-shadow: 0 -1px 8px light-dark(rgb(24 28 36 / 0.03), rgb(0 0 0 / 0.15));
	}
	.hint {
		margin-right: auto;
		color: var(--color-text-muted);
		font-size: var(--text-xs);
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
	kbd {
		padding: 0.25rem 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius-sm);
		font-size: 0.625rem;
	}
	.msg {
		margin: 0 0 var(--space-2);
		font-size: var(--text-sm);
		color: var(--color-danger);
	}
	/* Wraps instead of overflowing on a 320px phone when Cancel is visible. */
	.actions {
		flex-wrap: wrap;
		justify-content: flex-end;
	}
	.btn {
		white-space: nowrap;
	}
	.generate {
		flex: 2 1 10rem;
		max-width: 16rem;
		font-weight: 650;
	}
	@media (max-width: 767px) {
		.hint {
			display: none;
		}
		.generate {
			max-width: none;
		}
	}
	@media (min-width: 768px) and (max-width: 1099px) {
		.hint kbd {
			display: none;
		}
	}
</style>
