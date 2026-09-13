<script lang="ts">
	import '$lib/ui/tokens.css';
	import { onMount } from 'svelte';
	import { session } from '$lib/session.svelte';
	import { settingsState } from '$lib/settings.svelte';
	import Nav from '$lib/ui/Nav.svelte';
	import SignIn from '$lib/ui/SignIn.svelte';

	let { children } = $props();

	onMount(() => {
		session.load();
	});

	// Profile's theme preference overrides the system default once known;
	// "system" (and not-yet-loaded) leaves the media-query tokens in charge.
	$effect(() => {
		const theme = settingsState.data?.profile.theme;
		if (theme === 'light' || theme === 'dark') {
			document.documentElement.dataset.theme = theme;
		} else {
			delete document.documentElement.dataset.theme;
		}
	});
</script>

{#if session.loading}
	<p class="status">Loading…</p>
{:else if session.info?.authenticated}
	<div class="shell">
		<Nav />
		<main class="content">
			{@render children()}
		</main>
	</div>
{:else if session.error}
	<p class="status" role="alert">{session.error}</p>
{:else}
	<SignIn />
{/if}

<style>
	.status {
		padding: var(--space-4);
	}

	.shell {
		display: grid;
		grid-template-areas: 'top' 'content' 'nav';
		grid-template-rows: auto 1fr auto;
		min-height: 100dvh;
	}

	.content {
		grid-area: content;
		padding: var(--space-4);
		overflow-y: auto;
	}

	@media (min-width: 768px) {
		.shell {
			grid-template-areas: 'top top' 'nav content';
			grid-template-columns: 14rem 1fr;
			grid-template-rows: auto 1fr;
		}
	}
</style>
