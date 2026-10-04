<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { MediaInfo } from '$lib/contracts';
	import Icon from '$lib/ui/Icon.svelte';
	let {
		item,
		missing = false,
		onerror,
		fallback
	}: {
		item: MediaInfo;
		missing?: boolean;
		onerror?: () => void;
		fallback?: Snippet;
	} = $props();
</script>

{#if item.media_kind !== 'other' && item.state !== 'unavailable' && !missing}
	<img src={`/api/media/${item.id}/thumbnail`} alt="" loading="lazy" {onerror} />
{:else if fallback}
	{@render fallback()}
{:else}
	<Icon name={item.media_kind === 'video' ? 'video' : 'image'} size={24} />
{/if}

<style>
	img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
</style>
