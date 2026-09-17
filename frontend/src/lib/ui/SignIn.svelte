<script lang="ts">
	import { onMount } from 'svelte';
	import { session } from '$lib/session.svelte';
	import { describeApiError } from '$lib/api';

	let name = $state('');
	let password = $state('');
	let submitting = $state(false);
	let error = $state<string | null>(null);

	onMount(() => {
		session.loadProfiles();
	});

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
	<form onsubmit={submit}>
		<h1>Sign in</h1>
		<label>
			Profile
			<input
				name="name"
				bind:value={name}
				list="profile-names"
				autocomplete="username"
				required
			/>
			<datalist id="profile-names">
				{#each session.profiles as profile (profile.id)}
					<option value={profile.name}></option>
				{/each}
			</datalist>
		</label>
		<label>
			Password
			<input
				name="password"
				type="password"
				bind:value={password}
				autocomplete="current-password"
				required
			/>
		</label>
		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}
		<button type="submit" disabled={submitting}>
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
		padding: var(--space-4);
	}

	form {
		width: 100%;
		max-width: 22rem;
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	label {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: 0.875rem;
	}

	input {
		min-height: var(--touch-target);
		padding: 0 var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-bg);
	}

	button {
		min-height: var(--touch-target);
		padding: 0 var(--space-3);
		border: none;
		border-radius: var(--radius);
		background: var(--color-accent);
		color: var(--color-accent-text);
		font-weight: 600;
		cursor: pointer;
	}

	button:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.error {
		color: var(--color-danger);
		margin: 0;
	}
</style>
