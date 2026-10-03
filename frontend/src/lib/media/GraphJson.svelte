<script lang="ts">
	// The exact ComfyUI API graph a generation submitted, loaded on first open.
	import { api, describeApiError } from '$lib/api';

	let { generationId }: { generationId: string } = $props();

	let text = $state<string | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(false);
	let copied = $state(false);

	async function load(event: Event): Promise<void> {
		if (!(event.currentTarget as HTMLDetailsElement).open || text !== null || loading) return;
		loading = true;
		error = null;
		try {
			text = JSON.stringify(await api<unknown>(`/generations/${generationId}/graph`), null, 2);
		} catch (cause) {
			error = describeApiError(cause);
		} finally {
			loading = false;
		}
	}

	async function copy(): Promise<void> {
		try {
			await navigator.clipboard.writeText(text ?? '');
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			error = 'Copy is not available here; use Download instead.';
		}
	}

	function download(): void {
		const url = URL.createObjectURL(new Blob([text ?? ''], { type: 'application/json' }));
		const link = document.createElement('a');
		link.href = url;
		link.download = `workflow-${generationId}.json`;
		link.click();
		URL.revokeObjectURL(url);
	}
</script>

<details ontoggle={load}>
	<summary>Workflow JSON (API format)</summary>
	{#if loading}
		<p class="muted">Loading…</p>
	{:else if error}
		<p class="error" role="alert">{error}</p>
	{/if}
	{#if text !== null}
		<div class="row wrap actions">
			<button type="button" class="btn" onclick={copy}>{copied ? 'Copied' : 'Copy'}</button>
			<button type="button" class="btn" onclick={download}>Download</button>
		</div>
		<pre>{text}</pre>
	{/if}
</details>

<style>
	/* Bottom of the details column: when open, takes whatever height is left. */
	details {
		min-width: 0;
		flex: 0 0 auto;
	}
	details[open] {
		flex: 1 1 auto;
		min-height: 14rem;
		display: flex;
		flex-direction: column;
	}
	.error {
		overflow-wrap: anywhere;
	}
	.actions {
		margin: var(--space-1) 0;
	}
	pre {
		flex: 1 1 0;
		min-height: 0;
		margin: 0;
		overflow: auto;
		scrollbar-gutter: stable;
		white-space: pre;
		font-family: var(--font-mono);
		font-size: var(--text-sm);
	}
</style>
