<script lang="ts">
	import GenerationForm from '$lib/controls/GenerationForm.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<h1>Generate</h1>
{#key data.id}
	<GenerationForm workflowId={data.id} />
{/key}
