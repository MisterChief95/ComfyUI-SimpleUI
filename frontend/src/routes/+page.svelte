<script lang="ts">
	import { api, describeApiError } from '$lib/api';
	import type { Health } from '$lib/contracts';

	// One-shot fetch on component init; {#await} renders the three states.
	const health = api<Health>('/health');
</script>

<section>
	<h1>ComfyUI SimpleUI</h1>
	{#await health}
		<p>Checking the backend…</p>
	{:then result}
		<p>Backend {result.version} is reachable.</p>
	{:catch cause}
		<p role="alert">{describeApiError(cause)}</p>
	{/await}
	<p>Generate, Gallery, History, and Workflows land here as their own tasks complete.</p>
</section>
