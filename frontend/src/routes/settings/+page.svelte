<script lang="ts">
	import { onMount } from 'svelte';
	import { settingsState } from '$lib/settings.svelte';
	import { session } from '$lib/session.svelte';
	import { api, describeApiError } from '$lib/api';
	import type { SettingValue } from '$lib/contracts';
	import { HOST_FIELDS, PROFILE_GROUPS } from '$lib/settings/fields';
	import SettingField from '$lib/settings/SettingField.svelte';
	import SettingRow from '$lib/settings/SettingRow.svelte';

	const PACK_REPO_URL = 'https://github.com/MisterChief95/ComfyUI-SimpleUI-Nodes';

	onMount(() => {
		if (!settingsState.data) settingsState.load();
		void loadPack();
	});

	let savingKey = $state<string | null>(null);
	let saveError = $state<string | null>(null);
	let switching = $state(false);

	async function save(scope: 'profile' | 'host', key: string, value: SettingValue): Promise<void> {
		savingKey = key;
		saveError = null;
		try {
			if (scope === 'profile') await settingsState.setProfile(key, value);
			else await settingsState.setHost(key, value);
		} catch (cause) {
			saveError = describeApiError(cause);
		} finally {
			savingKey = null;
		}
	}

	async function switchProfile(): Promise<void> {
		switching = true;
		try {
			await session.logout();
		} finally {
			switching = false;
		}
	}

	type CatalogFreshness = {
		state: string;
		fetched_ms: string | null;
		cooldown_active: boolean;
		error: { message: string } | null;
	};
	let catalog = $state<CatalogFreshness | null>(null);
	let refreshing = $state(false);
	let catalogError = $state<string | null>(null);

	async function refreshCatalog(): Promise<void> {
		refreshing = true;
		catalogError = null;
		try {
			catalog = (await api<{ freshness: CatalogFreshness }>('/catalog/refresh', { method: 'POST' }))
				.freshness;
			void loadPack();
		} catch (cause) {
			catalogError = describeApiError(cause);
		} finally {
			refreshing = false;
		}
	}

	// Optional SimpleUI node pack (PACK-001). Derived from the cached catalog,
	// so it updates when the catalog is refreshed above.
	type PackStatus = {
		state: 'missing' | 'present' | 'outdated';
		detected_by: 'route' | 'class_types' | null;
		version: string | null;
		contract: number | null;
		supported_contract: number;
		repo_url: string;
		catalog_state: 'fresh' | 'stale' | 'unavailable';
	};
	let pack = $state<PackStatus | null>(null);
	async function loadPack(): Promise<void> {
		try {
			pack = await api<PackStatus>('/catalog/pack');
		} catch {
			pack = null; // optional: the card simply shows nothing to report
		}
	}
</script>

<div class="page stack">
	<h1>Settings</h1>

	{#if settingsState.loading && !settingsState.data}
		<p class="muted">Loading…</p>
	{:else if settingsState.error}
		<p class="error" role="alert">{settingsState.error}</p>
	{:else if settingsState.data}
		{@const data = settingsState.data}

		{#if saveError}
			<p class="error" role="alert">{saveError}</p>
		{/if}

		<p class="muted scope-note">
			Your profile settings apply only to {session.info?.profile?.name ?? 'your profile'}, on any
			device you sign in from.
		</p>

		<div class="cards">
			{#each PROFILE_GROUPS as group (group.title)}
				<section class="card">
					<h2>{group.title}</h2>
					{#each group.fields as field (field.key)}
						<SettingField
							id={`profile-${field.key}`}
							{field}
							value={data.profile[field.key]}
							disabled={savingKey === field.key}
							onchange={(value) => save('profile', field.key, value)}
						/>
					{/each}
				</section>
			{/each}

			<section class="card">
				<h2>Local host</h2>
				<p class="muted scope-note">
					{#if data.host_writable}
						Applies to this whole installation and every profile on it.
					{:else}
						Read-only from here: this is a local host setting. Change it in the browser on the
						computer running SimpleUI (http://localhost), not from another device.
					{/if}
				</p>
				{#each HOST_FIELDS as field (field.key)}
					<SettingField
						id={`host-${field.key}`}
						{field}
						value={data.host[field.key]}
						disabled={!data.host_writable || savingKey === field.key}
						onchange={(value) => save('host', field.key, value)}
					/>
				{/each}
			</section>

			<section class="card">
				<h2>Node catalog</h2>
				<SettingRow label="Refresh node catalog" inline>
					{#snippet help()}
						Re-read installed ComfyUI nodes after installing or updating a node pack. Reload the
						workflow afterwards.
						{#if catalog}
							{#if catalog.cooldown_active}
								Skipped: a refresh ran too recently, try again shortly.
							{:else if catalog.error}
								Failed: {catalog.error.message}
							{:else}
								Updated {new Date(Number(catalog.fetched_ms)).toLocaleTimeString()} ({catalog.state}).
							{/if}
						{/if}
						{#if catalogError}<span class="error" role="alert">{catalogError}</span>{/if}
					{/snippet}
					<button type="button" class="btn" onclick={refreshCatalog} disabled={refreshing}>
						{refreshing ? 'Refreshing…' : 'Refresh'}
					</button>
				</SettingRow>
			</section>

			<section class="card">
				<h2>SimpleUI node pack</h2>
				<SettingRow label="Status" inline>
					{#snippet help()}
						Optional ComfyUI nodes that unlock richer controls, such as a LoRA stack. Everything
						else works without them.
						{#if pack?.state === 'outdated'}
							This app supports pack contract {pack.supported_contract}, but the installed pack
							reports {pack.contract ?? 'no valid contract'}. Install a matching release.
						{:else if pack?.state === 'missing' && pack.catalog_state === 'unavailable'}
							ComfyUI has not been reached yet, so the pack could not be checked.
						{:else if pack?.state === 'present' && pack.detected_by === 'class_types'}
							Detected by its nodes; this pack version does not report its version.
						{/if}
						<a href={pack?.repo_url ?? PACK_REPO_URL} target="_blank" rel="noopener noreferrer"
							>Get the node pack</a
						>
					{/snippet}
					{#if pack?.state === 'present'}
						<span class="badge badge-success"
							>Installed{pack.version ? ` ${pack.version}` : ''}</span
						>
					{:else if pack?.state === 'outdated'}
						<span class="badge badge-warning"
							>Unsupported{pack.version ? ` ${pack.version}` : ''}</span
						>
					{:else if pack}
						<span class="badge">Not installed</span>
					{/if}
				</SettingRow>
			</section>

			<section class="card">
				<h2>Profile</h2>
				<SettingRow
					label="Signed in as"
					inline
					help={data.multi_user
						? 'Multi-user mode is on: each profile keeps its own inputs, history and outputs, protected by a password.'
						: 'Single-user mode: the Default profile is used without a password.'}
				>
					<strong>{session.info?.profile?.name ?? 'Default'}</strong>
				</SettingRow>
				{#if data.multi_user}
					<SettingRow label="Switch profile" help="Sign out and choose another profile." inline>
						<button type="button" class="btn" onclick={switchProfile} disabled={switching}>
							{switching ? 'Switching…' : 'Switch profile'}
						</button>
					</SettingRow>
				{/if}
			</section>
		</div>
	{/if}
</div>

<style>
	h1,
	h2 {
		margin: 0;
	}
	.page {
		max-width: 52rem;
	}
	.scope-note {
		margin: 0;
		font-size: var(--text-sm);
	}
	.cards {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}
	.card h2 {
		margin-bottom: var(--space-1);
	}

	.error {
		color: var(--color-danger);
	}
</style>
