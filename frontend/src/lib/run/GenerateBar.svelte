<script lang="ts">
	// Always-visible bar at the bottom of the run page (above the tab bar on a
	// phone, because the page is a flex column that ends here).
	import type { SeedPolicy } from '$lib/contracts';
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
		<label class="seed">
			<span class="sr-only">Seed policy</span>
			<select
				aria-label="Seed policy"
				title="Seed policy"
				value={run.seedPolicy}
				onchange={(e) => (run.seedChoice = e.currentTarget.value as SeedPolicy)}
			>
				<option value="random">Random seed</option>
				<option value="increment">Increment seed</option>
				<option value="fixed">Fixed seed</option>
			</select>
		</label>
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
		box-shadow: 0 -4px 12px light-dark(rgb(20 24 34 / 0.06), rgb(0 0 0 / 0.3));
	}
	.msg {
		margin: 0 0 var(--space-2);
		font-size: var(--text-sm);
		color: var(--color-danger);
	}
	/* Wraps instead of overflowing: with Cancel visible on a 320px phone the
	   seed select and Cancel share the first row and Generate takes the second. */
	.actions {
		flex-wrap: wrap;
		justify-content: flex-end;
	}
	.seed {
		flex: 1 1 8rem;
		min-width: 0;
		max-width: 11rem;
	}
	.btn {
		white-space: nowrap;
	}
	.generate {
		flex: 2 1 8rem;
		max-width: 16rem;
		font-weight: 650;
	}
	@media (max-width: 767px) {
		.seed {
			max-width: none;
		}
		.generate {
			max-width: none;
		}
	}
</style>
