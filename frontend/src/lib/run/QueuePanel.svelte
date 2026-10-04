<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { api, apiJson, describeApiError } from '$lib/api';
	import type { GenerationDetail, GenerationInfo, Page } from '$lib/contracts';
	import Sheet from '$lib/ui/Sheet.svelte';
	import { statusInfo } from './status';
	import { formatDate } from '$lib/media/format';
	import type { RunState } from './run.svelte';

	let { run, onclose }: { run: RunState; onclose: () => void } = $props();
	let open = $state(true);
	let active = $state.raw<GenerationInfo[]>([]);
	let recent = $state.raw<GenerationInfo[]>([]);
	let cursor = $state<string | null>(null);
	let loading = $state(false);
	let busy = $state<string | null>(null);
	let error = $state<string | null>(null);
	let message = $state<string | null>(null);
	let stopped = false;
	const retryKeys = new Map<string, string>();
	let vram = $state<string | null>(null);
	let freeing = $state(false);

	async function readVram(): Promise<void> {
		try {
			const v = await api<{ used_bytes: number | null; total_bytes: number | null }>(
				'/catalog/vram'
			);
			const gb = (n: number) => (n / 1024 ** 3).toFixed(1);
			vram =
				v.used_bytes === null || v.total_bytes === null
					? null
					: `VRAM ${gb(v.used_bytes)} / ${gb(v.total_bytes)} GB`;
		} catch {
			vram = null;
		}
	}

	async function freeVram(): Promise<void> {
		freeing = true;
		error = message = null;
		try {
			await api('/catalog/free', { method: 'POST' });
			message = 'Asked ComfyUI to unload models and free VRAM.';
			// ComfyUI applies /free on its next loop tick, not instantly.
			await new Promise((done) => setTimeout(done, 1500));
		} catch (cause) {
			error = describeApiError(cause);
		} finally {
			freeing = false;
			if (!stopped) await readVram();
		}
	}

	onMount(() => {
		void refresh(true);
		void readVram();
		const timer = setInterval(() => void refresh(), 3000);
		return () => {
			stopped = true;
			clearInterval(timer);
		};
	});

	async function refresh(withRecent = false): Promise<void> {
		if (loading || busy) return;
		loading = true;
		try {
			const page = await api<Page<GenerationInfo>>('/generations?status=active&limit=200');
			if (stopped) return;
			const changed = JSON.stringify(active) !== JSON.stringify(page.items);
			active = page.items;
			if (withRecent || changed) {
				const history = await api<Page<GenerationInfo>>('/generations?limit=20');
				if (stopped) return;
				const older = recent.filter((item) => !history.items.some((row) => row.id === item.id));
				recent = [...history.items, ...older];
				if (older.length === 0) cursor = history.next_cursor;
			}
		} catch (cause) {
			if (!stopped) error = describeApiError(cause);
		} finally {
			if (!stopped) loading = false;
		}
	}

	async function more(): Promise<void> {
		if (!cursor || loading || busy) return;
		loading = true;
		try {
			const page = await api<Page<GenerationInfo>>(
				`/generations?limit=20&cursor=${encodeURIComponent(cursor)}`
			);
			if (stopped) return;
			recent = [
				...recent,
				...page.items.filter((item) => !recent.some((old) => old.id === item.id))
			];
			cursor = page.next_cursor;
		} catch (cause) {
			if (!stopped) error = describeApiError(cause);
		} finally {
			if (!stopped) loading = false;
		}
	}

	async function action(item: GenerationInfo, kind: 'cancel' | 'retry'): Promise<void> {
		if (busy || loading) return;
		busy = item.id;
		error = message = null;
		try {
			if (kind === 'cancel') {
				await api(`/generations/${item.id}/cancel`, { method: 'POST' });
				message = 'Cancellation requested.';
			} else {
				// Keep the same key after a lost response: another click checks the
				// original submission rather than blindly executing it twice.
				const key = retryKeys.get(item.id) ?? crypto.randomUUID();
				retryKeys.set(item.id, key);
				const created = await apiJson<GenerationDetail>(`/generations/${item.id}/retry`, 'POST', {
					request_key: key
				});
				retryKeys.delete(item.id);
				message =
					created.status === 'submission_unknown'
						? 'Submission uncertain. Check the queue before submitting again.'
						: 'Submitted the saved execution again.';
				if (created.workflow_id === run.workflowId) await run.tracker.adopt(created);
			}
		} catch (cause) {
			error = describeApiError(cause);
		} finally {
			busy = null;
			if (!stopped) {
				await refresh(true);
				await run.tracker.refresh();
			}
		}
	}

	function name(item: GenerationInfo): string {
		return (
			run.workflows.find((workflow) => workflow.id === item.workflow_id)?.name ?? 'Saved generation'
		);
	}
	const finished = $derived(recent.filter((item) => !active.some((job) => job.id === item.id)));
</script>

{#snippet summary(item: GenerationInfo)}
	<strong>{name(item)}</strong>
	<p>{statusInfo(item).label} · {formatDate(item.created_ms)}</p>
{/snippet}

<Sheet bind:open title="Generation queue" {onclose}>
	<p class="muted">
		Your generations across workflows. Retry uses the saved values, including the original seed.
	</p>
	{#if error}<p class="error" role="alert">{error}</p>{/if}
	{#if message}<p role="status">{message}</p>{/if}
	<button
		class="btn"
		type="button"
		disabled={loading || busy !== null}
		onclick={() => refresh(true)}>Refresh</button
	>
	<button class="btn" type="button" disabled={freeing} onclick={freeVram}>Free VRAM</button>
	{#if vram}<span class="muted">{vram}</span>{/if}
	<h3>Active</h3>
	{#each active as item (item.id)}
		<article>
			{@render summary(item)}
			<div class="actions">
				{#if item.workflow_id}<a class="btn" href={resolve(`/generation/${item.workflow_id}`)}
						>Open workflow</a
					>{/if}
				<button
					class="btn"
					type="button"
					disabled={loading || busy !== null || !['queued', 'running'].includes(item.status)}
					onclick={() => action(item, 'cancel')}>Cancel</button
				>
			</div>
		</article>
	{:else}<p class="muted">{loading ? 'Loading…' : 'No active generations.'}</p>{/each}
	<h3>Recent submissions</h3>
	{#each finished as item (item.id)}
		<article>
			{@render summary(item)}
			<button
				class="btn"
				type="button"
				disabled={loading || busy !== null || !item.can_retry}
				onclick={() => action(item, 'retry')}>Retry saved execution</button
			>
			{#if !item.can_retry}<p class="muted">
					Retry unavailable: execution is uncertain or its snapshot was not retained.
				</p>{/if}
		</article>
	{:else}<p class="muted">No recent submissions.</p>{/each}
	{#if cursor}<button class="btn" type="button" disabled={loading || busy !== null} onclick={more}
			>Load more</button
		>{/if}
</Sheet>

<style>
	p {
		font-size: var(--text-sm);
		overflow-wrap: anywhere;
	}
	article {
		padding-block: var(--space-3);
		border-top: 1px solid var(--color-border);
		overflow-wrap: anywhere;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}
	.error {
		color: var(--color-danger);
	}
</style>
