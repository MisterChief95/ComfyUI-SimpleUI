<script lang="ts">
	// "/" has no content of its own: resume the last run page, else the picker.
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { lastWorkflow } from '$lib/ui/lastWorkflow.svelte';

	onMount(() => {
		goto(lastWorkflow.href, { replaceState: true });
	});
</script>

<p class="muted">Loading…</p>
