<script lang="ts">
	// Preview, base model and suggested trigger words for the selected model file.
	// Everything degrades silently: ComfyUI's preview route is experimental.
	import { api } from '$lib/api';
	import { insertIntoPrompt } from './promptTarget';

	let { folder, filename }: { folder: string; filename: string } = $props();

	let info = $state<{ base_model: string | null; trigger_words: string[] } | null>(null);
	let broken = $state(false);
	const query = $derived(`filename=${encodeURIComponent(filename)}`);

	$effect(() => {
		const wanted = query;
		broken = false;
		info = null;
		api<typeof info>(`/catalog/models/${folder}/info?${wanted}`)
			.then((result) => {
				if (wanted === query) info = result;
			})
			.catch(() => {});
	});
</script>

<div class="model">
	{#if !broken}
		<img
			src={`/api/catalog/models/${folder}/preview?${query}`}
			alt=""
			loading="lazy"
			onerror={() => (broken = true)}
		/>
	{:else}
		<div class="placeholder" aria-hidden="true"></div>
	{/if}
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
</div>

<style>
	.model {
		display: flex;
		gap: var(--space-2);
		margin-top: var(--space-2);
		align-items: flex-start;
	}
	img,
	.placeholder {
		width: 4.5rem;
		height: 4.5rem;
		flex: none;
		object-fit: cover;
		border-radius: var(--radius-sm);
		background: var(--color-surface-3);
	}
	.meta {
		display: grid;
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
