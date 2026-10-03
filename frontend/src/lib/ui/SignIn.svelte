<script lang="ts">
	import { onMount } from 'svelte';
	import { session } from '$lib/session.svelte';
	import { describeApiError } from '$lib/api';
	import Icon from './Icon.svelte';

	let name = $state('');
	let password = $state('');
	let submitting = $state(false);
	let error = $state<string | null>(null);
	let passwordField = $state<HTMLInputElement>();

	onMount(() => {
		session.loadProfiles();
	});

	function pick(profileName: string): void {
		name = profileName;
		passwordField?.focus();
	}

	async function submit(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		error = null;
		submitting = true;
		try {
			await session.login(name, password);
			password = '';
		} catch (cause) {
			error = describeApiError(cause);
		} finally {
			submitting = false;
		}
	}
</script>

<div class="sign-in">
	<form class="card stack" onsubmit={submit}>
		<h1>Sign in</h1>

		{#if session.profiles.length > 0}
			<div role="group" aria-label="Profiles" class="tiles">
				{#each session.profiles as profile (profile.id)}
					<button
						type="button"
						class="tile"
						aria-pressed={name === profile.name}
						onclick={() => pick(profile.name)}
					>
						<span class="avatar"><Icon name="user" size={22} /></span>
						<span class="tile-name">{profile.name}</span>
					</button>
				{/each}
			</div>
		{/if}

		<label>
			Profile
			<input
				name="name"
				bind:value={name}
				autocomplete="username"
				placeholder="Profile name"
				required
			/>
		</label>
		<label>
			Password
			<input
				name="password"
				type="password"
				bind:this={passwordField}
				bind:value={password}
				autocomplete="current-password"
				required
			/>
		</label>
		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}
		<button type="submit" class="btn btn-primary" disabled={submitting}>
			{submitting ? 'Signing in…' : 'Sign in'}
		</button>
	</form>
</div>

<style>
	.sign-in {
		min-height: 100dvh;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: var(--space-3);
	}

	form {
		width: 100%;
		max-width: 24rem;
		padding: var(--space-4);
		--gap: var(--space-3);
	}

	h1 {
		margin: 0;
		text-align: center;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: var(--text-sm);
		color: var(--color-text-muted);
	}

	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(6.5rem, 1fr));
		gap: var(--space-2);
	}
	.tile {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-1);
		min-width: 0;
		min-height: var(--touch-target);
		padding: var(--space-2);
		color: var(--color-text);
		background: var(--color-surface-2);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		cursor: pointer;
	}
	.tile:hover {
		border-color: var(--color-border-strong);
	}
	.tile[aria-pressed='true'] {
		border-color: var(--color-accent);
		background: var(--color-accent-soft);
	}
	.avatar {
		display: grid;
		place-items: center;
		width: 2.5rem;
		height: 2.5rem;
		border-radius: 50%;
		background: var(--color-surface-3);
		color: var(--color-text-muted);
	}
	.tile[aria-pressed='true'] .avatar {
		color: var(--color-accent);
	}
	.tile-name {
		max-width: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-weight: 600;
		font-size: var(--text-sm);
	}

	.error {
		color: var(--color-danger);
		margin: 0;
	}
</style>
