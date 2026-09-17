<script lang="ts">
	import { page } from '$app/stores';
	import { session } from '$lib/session.svelte';

	// Only routes that exist today. Gallery/History land here as their own
	// task (UI-003) adds the routes; this list is the single place to add them.
	const items = [
		{ href: '/', label: 'Home' },
		{ href: '/workflows', label: 'Workflows' },
		{ href: '/generation', label: 'Generate' },
		{ href: '/gallery', label: 'Gallery' },
		{ href: '/history', label: 'History' },
		{ href: '/settings', label: 'Settings' }
	];

	let signingOut = $state(false);

	async function signOut(): Promise<void> {
		signingOut = true;
		try {
			await session.logout();
		} finally {
			signingOut = false;
		}
	}
</script>

<header class="topbar">
	<span class="brand">ComfyUI SimpleUI</span>
	{#if session.info?.profile}
		<div class="profile">
			<span class="profile-name">{session.info.profile.name}</span>
			{#if session.info.multi_user}
				<button type="button" onclick={signOut} disabled={signingOut}>
					{signingOut ? 'Switching…' : 'Switch profile'}
				</button>
			{/if}
		</div>
	{/if}
</header>

<nav aria-label="Main">
	<ul>
		{#each items as item (item.href)}
			<li>
				<a href={item.href} aria-current={$page.url.pathname === item.href ? 'page' : undefined}>
					{item.label}
				</a>
			</li>
		{/each}
	</ul>
</nav>

<style>
	.topbar {
		grid-area: top;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		padding: var(--space-3);
		border-bottom: 1px solid var(--color-border);
		background: var(--color-bg-elevated);
	}

	.brand {
		font-weight: 600;
	}

	.profile {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		font-size: 0.875rem;
	}

	.profile-name {
		color: var(--color-text-muted);
	}

	.profile button {
		min-height: var(--touch-target);
		padding: 0 var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-bg);
		cursor: pointer;
	}

	.profile button:disabled {
		opacity: 0.6;
		cursor: default;
	}

	nav {
		grid-area: nav;
		background: var(--color-bg-elevated);
		border-top: 1px solid var(--color-border);
	}

	nav ul {
		display: flex;
		justify-content: space-around;
		list-style: none;
		margin: 0;
		padding: var(--space-2) max(var(--space-2), env(safe-area-inset-right))
			max(var(--space-2), env(safe-area-inset-bottom)) max(var(--space-2), env(safe-area-inset-left));
	}

	nav a {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: var(--touch-target);
		min-width: var(--touch-target);
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius);
		color: inherit;
		text-decoration: none;
	}

	nav a[aria-current='page'] {
		background: var(--color-accent);
		color: var(--color-accent-text);
	}

	@media (min-width: 768px) {
		nav {
			border-top: none;
			border-right: 1px solid var(--color-border);
		}

		nav ul {
			flex-direction: column;
			align-items: stretch;
			padding: var(--space-3);
			gap: var(--space-1);
		}

		nav a {
			justify-content: flex-start;
		}
	}
</style>
