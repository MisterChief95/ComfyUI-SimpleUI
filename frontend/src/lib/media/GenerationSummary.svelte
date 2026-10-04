<script lang="ts">
	import type { GenerationDetail } from '$lib/contracts';
	import { formatDate } from './format';
	import { workflowNames } from './workflowNames.svelte';
	let {
		detail,
		showIdentity = false,
		statusClass
	}: {
		detail: GenerationDetail;
		showIdentity?: boolean;
		statusClass?: string;
	} = $props();
	const badge = $derived(
		statusClass ??
			(detail.status === 'succeeded'
				? 'badge-success'
				: detail.status === 'failed'
					? 'badge-danger'
					: '')
	);
</script>

<div class="row wrap">
	<span class="badge {badge}">{detail.status}</span>
	<span class="badge">{detail.output_state}</span>
</div>
{#if detail.workflow_id}
	<p>Workflow: <strong>{workflowNames.name(detail.workflow_id) ?? detail.workflow_id}</strong></p>
{/if}
{#if showIdentity}
	<p class="muted small">{formatDate(detail.created_ms)} · <code>{detail.id}</code></p>
{/if}
{#if detail.error}<pre class="error">{JSON.stringify(detail.error, null, 2)}</pre>{/if}

<style>
	p,
	pre {
		margin: 0;
	}
	.small,
	pre {
		font-size: var(--text-sm);
	}
	pre {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font-family: var(--font-mono);
	}
	.error {
		color: var(--color-danger);
	}
</style>
