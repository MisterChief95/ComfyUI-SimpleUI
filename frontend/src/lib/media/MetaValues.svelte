<script lang="ts">
	// Saved effective values as readable key/value rows (nested values expand) with a raw JSON toggle.
	// Keys are shown exactly as stored (e.g. "3:seed"): labels from the current mapping could differ from
	// what the run used, so none are looked up here.
	let { values }: { values: Record<string, unknown> } = $props();

	let raw = $state(false);

	const isBranch = (v: unknown): v is object => typeof v === 'object' && v !== null;
	const entries = (v: object): [string, unknown][] => Object.entries(v);
	const scalar = (v: unknown): string => (typeof v === 'string' ? v : String(v));
</script>

{#snippet rows(obj: object)}
	<dl>
		{#each entries(obj) as [key, value] (key)}
			{#if isBranch(value)}
				<details>
					<summary
						><span class="key">{key}</span>
						<span class="muted">{Array.isArray(value) ? `[${value.length}]` : '{…}'}</span></summary
					>
					{@render rows(value)}
				</details>
			{:else}
				<div class="kv">
					<dt class="key">{key}</dt>
					<dd>{scalar(value)}</dd>
				</div>
			{/if}
		{/each}
	</dl>
{/snippet}

<label class="row"><input type="checkbox" bind:checked={raw} /> Raw JSON</label>
{#if raw}
	<pre>{JSON.stringify(values, null, 2)}</pre>
{:else}
	{@render rows(values)}
{/if}

<style>
	dl {
		margin: 0;
		padding-left: var(--space-2);
		display: grid;
		gap: var(--space-1);
	}
	.kv {
		display: grid;
		gap: 0;
	}
	dt,
	summary {
		font-size: var(--text-sm);
		color: var(--color-text-muted);
	}
	summary {
		cursor: pointer;
	}
	dd {
		margin: 0;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	pre {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
</style>
