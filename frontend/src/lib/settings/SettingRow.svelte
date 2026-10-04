<script lang="ts">
	import type { Snippet } from 'svelte';
	let {
		label,
		help,
		for: id,
		inline = false,
		children
	}: {
		label: string;
		help: string | Snippet;
		for?: string;
		inline?: boolean;
		children: Snippet;
	} = $props();
</script>

<div class="setting" class:inline>
	{#snippet text()}
		<span class="label">{label}</span>
		<span class="help muted"
			>{#if typeof help === 'string'}{help}{:else}{@render help()}{/if}</span
		>
	{/snippet}
	{#if id}<label class="text" for={id}>{@render text()}</label>
	{:else}<div class="text">{@render text()}</div>{/if}
	<div class="control">{@render children()}</div>
</div>

<style>
	.setting {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-3) 0;
		border-top: 1px solid var(--color-border);
	}
	.setting:first-of-type {
		border-top: 0;
	}
	.text {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		min-width: 0;
	}
	.label {
		font-weight: 600;
	}
	.help {
		font-size: var(--text-sm);
	}
	.control {
		min-width: 0;
	}
	.setting.inline {
		flex-direction: row;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
	}
	.setting.inline .control {
		flex: none;
	}
	@media (min-width: 640px) {
		.setting {
			flex-direction: row;
			align-items: center;
			justify-content: space-between;
			gap: var(--space-4);
		}
		.setting .text {
			flex: 1 1 0;
		}
		.setting .control {
			flex: 0 1 18rem;
		}
		.setting.inline .control {
			flex: none;
		}
	}
</style>
