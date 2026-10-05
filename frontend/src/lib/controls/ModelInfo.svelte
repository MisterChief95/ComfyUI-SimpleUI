<script lang="ts">
	// Base model and suggested trigger words for the selected model file.
	import { api } from '$lib/api';
	import { insertIntoPrompt } from './promptTarget';

	let { folder, filename }: { folder: string; filename: string } = $props();

	let info = $state<{ base_model: string | null; trigger_words: string[] } | null>(null);
	const query = $derived(`filename=${encodeURIComponent(filename)}`);

	$effect(() => {
		const wanted = query;
		info = null;
		api<typeof info>(`/catalog/models/${folder}/info?${wanted}`)
			.then((result) => {
				if (wanted === query) info = result;
			})
			.catch(() => {});
	});
</script>

{#if info?.base_model || info?.trigger_words.length}
	<div class="meta">
		{#if info?.base_model}<span class="muted">Base: {info.base_model}</span>{/if}
		{#if info?.trigger_words.length}
			<ul aria-label="Suggested trigger words">
				{#each info.trigger_words as word (word)}
					<li>
						<button
							type="button"
							class="btn"
							title="Insert into prompt"
							onclick={() => insertIntoPrompt(`${word}, `)}>{word}</button
						>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
{/if}

<style>
	.meta {
		display: grid;
		margin-top: var(--space-2);
		gap: var(--space-1);
		min-width: 0;
		font-size: var(--text-sm);
	}
	ul {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}
</style>
