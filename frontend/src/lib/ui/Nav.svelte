<script lang="ts">
	// App navigation. One <nav> that reshapes with the viewport:
	//   phone  (< 768px)    fixed bottom tab bar, icon + label; no top bar.
	//   tablet (768-1199)   left icon rail (labels via title/aria).
	//   desktop (>= 1200)   left rail with brand and labels.
	// Profile: on desktop/tablet the signed-in profile (and "Switch profile" when
	// multi_user) sits at the bottom of the rail. On phone the tab bar has no
	// room, so a slim header with the same controls appears ONLY when multi_user;
	// the shell (routes/+layout.svelte) then sets --header-h for it.
	import { page } from '$app/state';
	import { session } from '$lib/session.svelte';
	import { lastWorkflow } from './lastWorkflow.svelte';
	import Icon, { type IconName } from './Icon.svelte';
	import { navCollapsed } from './navCollapsed.svelte';

	// The one place to add a destination. Generate returns to the last run page.
	const items = $derived<{ match: string; href: string; label: string; icon: IconName }[]>([
		{ match: '/generation', href: lastWorkflow.href, label: 'Generate', icon: 'generate' },
		{ match: '/workflows', href: '/workflows', label: 'Workflows', icon: 'workflows' },
		{ match: '/gallery', href: '/gallery', label: 'Gallery', icon: 'gallery' },
		{ match: '/history', href: '/history', label: 'History', icon: 'history' },
		{ match: '/settings', href: '/settings', label: 'Settings', icon: 'settings' }
	]);

	const active = (match: string): boolean =>
		page.url.pathname === match || page.url.pathname.startsWith(`${match}/`);

	let signingOut = $state(false);

	async function signOut(): Promise<void> {
		signingOut = true;
		try {
			await session.logout();
		} finally {
			signingOut = false;
		}
	}

	const profile = $derived(session.info?.profile ?? null);
	const multiUser = $derived(session.info?.multi_user ?? false);
</script>

{#if profile && multiUser}
	<header class="phone-bar">
		<span class="who"><Icon name="user" size={16} /> {profile.name}</span>
		<button type="button" class="btn btn-ghost" onclick={signOut} disabled={signingOut}>
			{signingOut ? 'Switching…' : 'Switch profile'}
		</button>
	</header>
{/if}

<nav aria-label="Main" class:collapsed={navCollapsed.value}>
	<div class="brand" aria-hidden="true">
		<span class="mark"><Icon name="workflows" size={20} /></span>
		<div class="brand-name"><strong>SimpleUI</strong><span>ComfyUI workspace</span></div>
	</div>
	<span class="nav-caption" aria-hidden="true">Workspace</span>
	<ul>
		{#each items as item (item.match)}
			<li>
				<a
					href={item.href}
					title={item.label}
					aria-current={active(item.match) ? 'page' : undefined}
				>
					<Icon name={item.icon} size={20} />
					<span class="label">{item.label}</span>
				</a>
			</li>
		{/each}
	</ul>
	<button
		type="button"
		class="collapse"
		title={navCollapsed.value ? 'Expand sidebar' : 'Collapse sidebar'}
		aria-label={navCollapsed.value ? 'Expand sidebar' : 'Collapse sidebar'}
		aria-expanded={!navCollapsed.value}
		onclick={() => navCollapsed.toggle()}
	>
		<Icon name="menu" size={20} />
	</button>
	{#if profile}
		<div class="profile">
			<span class="who" title={profile.name}
				><Icon name="user" size={16} /><span class="who-name">{profile.name}</span></span
			>
			{#if multiUser}
				<button
					type="button"
					class="btn btn-ghost switch"
					title="Switch profile"
					aria-label="Switch profile"
					onclick={signOut}
					disabled={signingOut}
				>
					<span class="switch-icon"><Icon name="switch" size={18} /></span>
					<span class="switch-label">{signingOut ? 'Switching…' : 'Switch profile'}</span>
				</button>
			{/if}
		</div>
	{/if}
</nav>

<style>
	/* ---- phone: slim header (multi_user only) + bottom tab bar ---- */
	.phone-bar {
		position: sticky;
		top: 0;
		z-index: 20;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		height: var(--header-h);
		padding: 0 max(var(--space-2), env(safe-area-inset-right)) 0
			max(var(--space-3), env(safe-area-inset-left));
		background: var(--color-surface-1);
		border-bottom: 1px solid var(--color-border);
		font-size: var(--text-sm);
	}
	.phone-bar .btn {
		font-size: var(--text-sm);
	}
	.who {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		min-width: 0;
		color: var(--color-text-muted);
		font-weight: 550;
	}

	nav {
		position: fixed;
		inset: auto 0 0 0;
		z-index: 30;
		display: flex;
		background: var(--color-surface-1);
		border-top: 1px solid var(--color-border);
		padding: 0 env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);
	}
	.brand,
	.nav-caption,
	.profile,
	.collapse {
		display: none;
	}
	ul {
		display: flex;
		flex: 1;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	li {
		flex: 1 1 0;
		min-width: 0;
	}
	a {
		position: relative;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.2rem;
		height: 3.5rem;
		color: var(--color-text-muted);
		font-size: 0.6875rem;
		font-weight: 600;
		text-decoration: none;
		-webkit-user-select: none;
		user-select: none;
		transition:
			background 0.15s var(--ease),
			color 0.15s var(--ease);
	}
	a[aria-current='page'] {
		color: var(--color-accent);
	}
	a[aria-current='page']::before {
		content: '';
		position: absolute;
		top: 0;
		left: 28%;
		right: 28%;
		height: 2px;
		border-radius: 0 0 2px 2px;
		background: var(--color-accent);
	}
	a:active {
		background: var(--color-surface-2);
	}

	/* ---- tablet: icon rail ---- */
	@media (min-width: 768px) {
		.phone-bar {
			display: none;
		}
		nav {
			position: sticky;
			inset: auto;
			top: 0;
			grid-column: 1;
			flex-direction: column;
			align-items: stretch;
			gap: var(--space-2);
			height: 100dvh;
			padding: var(--space-3) var(--space-2);
			padding-left: max(var(--space-2), env(safe-area-inset-left));
			border-top: 0;
			border-right: 1px solid var(--color-border);
			overflow-y: auto;
			scrollbar-gutter: stable;
		}
		.brand {
			display: flex;
			align-items: center;
			justify-content: center;
			gap: 0.6rem;
			padding-block: var(--space-2) var(--space-4);
		}
		.mark {
			display: grid;
			place-items: center;
			width: 2rem;
			height: 2rem;
			border-radius: var(--radius);
			background: var(--color-accent-soft);
			color: var(--color-accent);
			font-weight: 800;
		}
		.brand-name {
			display: none;
		}
		ul {
			flex: none;
			flex-direction: column;
			gap: var(--space-1);
		}
		a {
			flex-direction: row;
			justify-content: center;
			height: var(--touch-target);
			border-radius: var(--radius);
		}
		a:hover {
			background: var(--color-surface-2);
			color: var(--color-text);
		}
		a[aria-current='page'] {
			background: var(--color-accent-soft);
		}
		a[aria-current='page']::before {
			display: none;
		}
		.label {
			/* Icon-only on the rail; the title attribute and aria name remain. */
			position: absolute;
			width: 1px;
			height: 1px;
			overflow: hidden;
			clip-path: inset(50%);
			white-space: nowrap;
		}
		.profile {
			display: flex;
			flex-direction: column;
			align-items: center;
			gap: var(--space-1);
			margin-top: auto;
		}
		.who-name,
		.switch-label {
			display: none;
		}
		.switch {
			width: var(--touch-target);
			padding: 0;
		}
	}

	/* ---- desktop: labelled rail ---- */
	@media (min-width: 1200px) {
		nav {
			min-width: 0;
			padding: var(--space-3) 0.75rem;
			padding-left: max(0.75rem, env(safe-area-inset-left));
			overflow-x: hidden;
			scrollbar-gutter: auto;
		}
		/* A fixed icon column keeps every row anchored while the shell changes width. */
		.brand,
		a,
		.profile .who,
		.switch {
			display: grid;
			grid-template-columns: var(--touch-target) minmax(0, 1fr);
			align-items: center;
			gap: 0;
		}
		.brand {
			padding: 0;
			min-height: var(--touch-target);
		}
		.mark {
			justify-self: center;
		}
		a,
		.switch {
			padding: 0;
			font-size: var(--text-sm);
		}
		a :global(svg),
		.profile .who :global(svg) {
			justify-self: center;
		}
		.brand-name {
			display: flex;
			flex-direction: column;
			gap: 0.15rem;
			strong {
				font-size: var(--text-lg);
				letter-spacing: -0.03em;
			}
			span {
				font-size: 0.6875rem;
				color: var(--color-text-muted);
			}
		}
		.nav-caption {
			display: block;
			padding: 0 0.75rem;
			font-size: 0.625rem;
			font-weight: 650;
			letter-spacing: 0.12em;
			text-transform: uppercase;
			color: var(--color-text-faint);
		}
		.label {
			position: static;
			width: auto;
			height: auto;
			clip-path: none;
		}
		.brand-name,
		.nav-caption,
		.label,
		.who-name,
		.switch-label {
			min-width: 0;
			overflow: hidden;
			white-space: nowrap;
			transition:
				opacity 160ms var(--ease),
				transform var(--sidebar-duration) var(--ease);
		}
		nav.collapsed {
			.brand-name,
			.nav-caption,
			.label,
			.who-name,
			.switch-label {
				opacity: 0;
				transform: translateX(-0.375rem);
			}
		}
		.collapse {
			display: flex;
			align-items: center;
			justify-content: center;
			align-self: flex-start;
			width: var(--touch-target);
			height: var(--touch-target);
			flex-shrink: 0;
			padding: 0;
			border: 0;
			border-radius: var(--radius);
			background: none;
			color: var(--color-text-muted);
			cursor: pointer;
			margin-top: auto;
		}
		.collapse:hover {
			background: var(--color-surface-2);
			color: var(--color-text);
		}
		nav .profile {
			align-items: stretch;
			margin-top: 0;
			padding-top: var(--space-3);
			border-top: 1px solid var(--color-border);
		}
		.who-name,
		.switch-label {
			display: block;
			text-overflow: ellipsis;
		}
		.switch {
			width: 100%;
			min-height: var(--touch-target);
			text-align: left;
		}
		.switch-icon {
			display: grid;
			place-items: center;
		}
	}
</style>
