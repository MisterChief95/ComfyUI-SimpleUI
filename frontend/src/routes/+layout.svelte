<script lang="ts">
	import '$lib/ui/tokens.css';
	import { onMount } from 'svelte';
	import { session } from '$lib/session.svelte';
	import { settingsState } from '$lib/settings.svelte';
	import Nav from '$lib/ui/Nav.svelte';
	import { navCollapsed } from '$lib/ui/navCollapsed.svelte';
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
	<div
		class="shell"
		class:has-header={session.info.multi_user}
		class:collapsed={navCollapsed.value}
	>
		<Nav />
		<main class="app-main">
			{@render children()}
		</main>
	</div>
{:else if session.error}
	<p class="status" role="alert">{session.error}</p>
{:else}
	<SignIn />
{/if}

<style>
	/* Shell contract (see the top of lib/ui/tokens.css): --header-h, --nav-h and
	   --app-h describe the chrome so pages can size themselves. Custom properties
	   that use var() resolve where declared, so --app-h is restated here. */
	.status {
		padding: var(--space-4);
	}

	.shell {
		--nav-h: calc(3.5rem + env(safe-area-inset-bottom));
		--header-h: 0px;
		--app-h: calc(100dvh - var(--header-h) - var(--nav-h));
		min-height: 100dvh;
	}

	.shell.has-header {
		--header-h: 2.75rem;
	}

	.app-main {
		min-width: 0;
		/* Phone: the fixed tab bar overlays the bottom of the document. */
		padding: var(--page-pad);
		padding-bottom: calc(var(--page-pad) + var(--nav-h));
	}

	/* Full-bleed pages (.page-full) own the whole content area. */
	.app-main:has(> :global(.page-full)) {
		padding: 0 0 var(--nav-h);
	}

	@media (min-width: 768px) {
		.shell,
		.shell.has-header {
			--nav-h: 0px;
			--header-h: 0px;
			--rail-w: 4.5rem;
			display: grid;
			grid-template-columns: var(--rail-w) minmax(0, 1fr);
		}
		.app-main {
			grid-column: 2;
		}
	}

	@media (min-width: 1200px) {
		.shell,
		.shell.has-header {
			--rail-w: 13.5rem;
		}
		.shell.collapsed,
		.shell.collapsed.has-header {
			--rail-w: 4.5rem;
		}
	}
</style>
