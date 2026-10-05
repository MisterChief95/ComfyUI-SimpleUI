<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { api, describeApiError } from '$lib/api';
	import type { ChainInfo, ChainRun } from '$lib/contracts';
	import { workflowNames } from '$lib/media/workflowNames.svelte';

	type Draft = { workflow_id: string; binding_id: string; node: string; ordinal: number };

	let chains = $state.raw<ChainInfo[]>([]);
	let runs = $state.raw<Record<string, ChainRun>>({});
	let name = $state('');
	let stages = $state<Draft[]>([
		{ workflow_id: '', binding_id: '', node: '', ordinal: 0 },
		{ workflow_id: '', binding_id: '', node: '', ordinal: 0 }
	]);
	let error = $state<string | null>(null);
	let timer: ReturnType<typeof setInterval> | undefined;

	async function load(): Promise<void> {
		chains = (await api<{ items: ChainInfo[] }>('/chains')).items;
	}

	async function refreshRuns(): Promise<void> {
		for (const run of Object.values(runs)) {
			if (run.status === 'running') {
				runs = { ...runs, [run.id]: await api<ChainRun>(`/chains/runs/${run.id}`) };
			}
		}
	}

	onMount(() => {
		workflowNames.load();
		load().catch((cause) => (error = describeApiError(cause)));
		timer = setInterval(() => refreshRuns().catch(() => {}), 3000);
	});
	onDestroy(() => clearInterval(timer));

	const post = (path: string, body?: unknown) =>
		api<ChainRun>(path, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(body ?? {})
		});

	async function create(): Promise<void> {
		error = null;
		try {
			await api('/chains', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					name,
					stages: stages.map((stage, index) => ({
						workflow_id: stage.workflow_id,
						links:
							index > 0 && stage.binding_id
								? [
										{
											binding_id: stage.binding_id,
											from_output_node: stage.node,
											ordinal: stage.ordinal
										}
									]
								: []
					}))
				})
			});
			name = '';
			await load();
		} catch (cause) {
			error = describeApiError(cause);
		}
	}

	async function act(promise: Promise<ChainRun>): Promise<void> {
		error = null;
		try {
			const run = await promise;
			runs = { ...runs, [run.id]: run };
		} catch (cause) {
			error = describeApiError(cause);
		}
	}

	const start = (chain: ChainInfo) =>
		act(post(`/chains/${chain.id}/runs`, { request_key: crypto.randomUUID() }));

	async function remove(chain: ChainInfo): Promise<void> {
		await api(`/chains/${chain.id}`, { method: 'DELETE' }).catch(
			(cause) => (error = describeApiError(cause))
		);
		await load();
	}

	const workflowName = (id: string) => workflowNames.list.find((w) => w.id === id)?.name ?? id;
</script>

<div class="page stack">
	<h1>Chains</h1>
	<p class="muted">
		Run workflows one after another. A stage starts only when the previous one succeeded and its
		output was saved; the run pauses on anything else.
	</p>
	{#if error}<p class="card notice" role="alert">{error}</p>{/if}

	{#each chains as chain (chain.id)}
		<section class="card stack" style:--gap="var(--space-2)">
			<h2>{chain.name}</h2>
			<ol>
				{#each chain.stages as stage, index (index)}
					<li>
						{workflowName(stage.workflow_id)}
						{#each stage.links as link (link.binding_id)}
							<span class="muted">
								← output of node {link.from_output_node} #{link.ordinal} into {link.binding_id}
							</span>
						{/each}
					</li>
				{/each}
			</ol>
			<div class="row">
				<button class="btn btn-primary" onclick={() => start(chain)}>Run</button>
				<button class="btn" onclick={() => remove(chain)}>Delete</button>
			</div>
			{#each Object.values(runs).filter((r) => r.chain_id === chain.id) as run (run.id)}
				<div class="card">
					<strong>{run.status}</strong>, stage {run.stage_index + 1} of {chain.stages.length}
					{#if run.error}<p role="alert">{run.error.message}</p>{/if}
					<div class="row">
						{#if run.status === 'paused'}
							<button class="btn" onclick={() => act(post(`/chains/runs/${run.id}/resume`))}>
								Resume
							</button>
						{/if}
						{#if run.status === 'running' || run.status === 'paused'}
							<button class="btn" onclick={() => act(post(`/chains/runs/${run.id}/cancel`))}>
								Cancel
							</button>
						{/if}
					</div>
				</div>
			{/each}
		</section>
	{/each}

	<form
		class="card stack"
		style:--gap="var(--space-2)"
		onsubmit={(event) => {
			event.preventDefault();
			create();
		}}
	>
		<h2>New chain</h2>
		<input placeholder="Name" bind:value={name} required />
		{#each stages as stage, index (index)}
			<fieldset class="stack" style:--gap="var(--space-1)">
				<legend>Stage {index + 1}</legend>
				<select bind:value={stage.workflow_id} required>
					<option value="" disabled>Workflow</option>
					{#each workflowNames.list as workflow (workflow.id)}
						<option value={workflow.id}>{workflow.name}</option>
					{/each}
				</select>
				{#if index > 0}
					<input placeholder="File input binding id (e.g. 2:image)" bind:value={stage.binding_id} />
					<input placeholder="Previous stage output node id" bind:value={stage.node} />
					<input type="number" min="0" aria-label="Output ordinal" bind:value={stage.ordinal} />
				{/if}
			</fieldset>
		{/each}
		<div class="row">
			<button
				type="button"
				class="btn"
				disabled={stages.length >= 8}
				onclick={() => stages.push({ workflow_id: '', binding_id: '', node: '', ordinal: 0 })}
			>
				Add stage
			</button>
			<button class="btn btn-primary" type="submit">Save chain</button>
		</div>
	</form>
</div>
