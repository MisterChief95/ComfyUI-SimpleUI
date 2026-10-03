<script lang="ts">
	import DesignerPage from '$lib/designer/DesignerPage.svelte';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
</script>

{#key data.id}
	<DesignerPage workflowId={data.id} />
{/key}
