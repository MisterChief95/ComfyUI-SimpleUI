<script lang="ts">
	import RunView from '$lib/run/RunView.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

{#key data.id}
	<RunView workflowId={data.id} />
{/key}
