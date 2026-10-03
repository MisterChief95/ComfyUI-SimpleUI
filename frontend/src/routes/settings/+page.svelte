<script lang="ts">
	import { onMount } from 'svelte';
	import { settingsState } from '$lib/settings.svelte';
	import { session } from '$lib/session.svelte';
	import { api, describeApiError } from '$lib/api';
	import type { SettingValue } from '$lib/contracts';

	type Field =
		| { key: string; label: string; help: string; type: 'text' | 'number' | 'checkbox' }
		| { key: string; label: string; help: string; type: 'select'; options: readonly string[] };

	// Mirrors backend/app/settings/service.py HOST_SETTINGS. Keep both in sync.
	const HOST_FIELDS: Field[] = [
		{
			key: 'comfy_url',
			label: 'ComfyUI URL',
			help: 'Where the ComfyUI server on this computer listens.',
			type: 'text'
		},
		{
			key: 'comfy_input_dir',
			label: 'ComfyUI input folder',
			help: 'Uploaded input files are copied here.',
			type: 'text'
		},
		{
			key: 'comfy_output_dir',
			label: 'ComfyUI output folder',
			help: 'Generated media is read from here.',
			type: 'text'
		},
		{
			key: 'upload_max_bytes',
			label: 'Upload size limit (bytes)',
			help: 'Largest single upload accepted.',
			type: 'number'
		},
		{
			key: 'pending_cap',
			label: 'Pending generation cap',
			help: 'Most queued generations allowed at once.',
			type: 'number'
		},
		{
			key: 'catalog_refresh_min_seconds',
			label: 'Catalog refresh cooldown (seconds)',
			help: 'Minimum time between node catalog refreshes.',
			type: 'number'
		},
		{
			key: 'backup_dir',
			label: 'Backup folder',
			help: 'Where database backups are written.',
			type: 'text'
		}
	];

	// Mirrors backend/app/settings/service.py PROFILE_SETTINGS. Keep both in sync.
	const PROFILE_GROUPS: { title: string; fields: Field[] }[] = [
		{
			title: 'Appearance',
			fields: [
				{
					key: 'theme',
					label: 'Theme',
					help: 'Follow the system, or force light or dark.',
					type: 'select',
					options: ['system', 'light', 'dark']
				},
				{
					key: 'density',
					label: 'Density',
					help: 'Compact fits more controls on screen.',
					type: 'select',
					options: ['comfortable', 'compact']
				}
			]
		},
		{
			title: 'Generation',
			fields: [
				{
					key: 'show_advanced',
					label: 'Show advanced controls',
					help: 'Reveal rarely used workflow controls.',
					type: 'checkbox'
				},
				{
					key: 'seed_policy',
					label: 'Seed policy',
					help: 'How the seed changes after each run.',
					type: 'select',
					options: ['fixed', 'random', 'increment']
				},
				{
					key: 'live_previews',
					label: 'Live previews',
					help: 'Show intermediate images while generating.',
					type: 'checkbox'
				},
				{
					key: 'video_enabled',
					label: 'Video generation',
					help: 'Allow workflows that produce video.',
					type: 'checkbox'
				},
				{
					key: 'completion_sound',
					label: 'Completion sound',
					help: 'Play a sound when a generation finishes.',
					type: 'checkbox'
				}
			]
		},
		{
			title: 'Gallery',
			fields: [
				{
					key: 'thumbnail_size',
					label: 'Thumbnail size',
					help: 'Size of tiles in the gallery grid.',
					type: 'select',
					options: ['small', 'medium', 'large']
				},
				{
					key: 'gallery_autoplay',
					label: 'Autoplay videos in gallery',
					help: 'Start videos as soon as they open.',
					type: 'checkbox'
				},
				{
					key: 'gallery_page_size',
					label: 'Gallery page size',
					help: 'How many items to load at a time.',
					type: 'number'
				}
			]
		},
		{
			title: 'Privacy and history',
			fields: [
				{
					key: 'store_history',
					label: 'Store prompt/workflow history',
					help: 'Keep prompts and inputs so past generations can be reused. Turning this off does not delete media.',
					type: 'checkbox'
				}
			]
		}
	];

	onMount(() => {
		if (!settingsState.data) settingsState.load();
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
		} catch (cause) {
			catalogError = describeApiError(cause);
		} finally {
			refreshing = false;
		}
	}

	function numberValue(event: Event): number {
		return Number((event.currentTarget as HTMLInputElement).value);
	}

	function textValue(event: Event): string {
		return (event.currentTarget as HTMLInputElement | HTMLSelectElement).value;
	}

	function checkedValue(event: Event): boolean {
		return (event.currentTarget as HTMLInputElement).checked;
	}
</script>

{#snippet row(
	scope: 'profile' | 'host',
	field: Field,
	values: Record<string, SettingValue>,
	writable: boolean
)}
	{@const id = `${scope}-${field.key}`}
	{@const busy = savingKey === field.key}
	<div class="setting" class:inline={field.type === 'checkbox'}>
		<label class="text" for={id}>
			<span class="label">{field.label}</span>
			<span class="help muted">{field.help}</span>
		</label>
		<div class="control">
			{#if field.type === 'checkbox'}
				<input
					{id}
					type="checkbox"
					class="switch"
					role="switch"
					checked={Boolean(values[field.key])}
					disabled={!writable || busy}
					onchange={(e) => save(scope, field.key, checkedValue(e))}
				/>
			{:else if field.type === 'select'}
				<select
					{id}
					value={String(values[field.key] ?? field.options[0])}
					disabled={!writable || busy}
					onchange={(e) => save(scope, field.key, textValue(e))}
				>
					{#each field.options as option (option)}
						<option value={option}>{option}</option>
					{/each}
				</select>
			{:else if field.type === 'number'}
				<input
					{id}
					type="number"
					min="0"
					value={Number(values[field.key] ?? 0)}
					disabled={!writable || busy}
					onchange={(e) => save(scope, field.key, numberValue(e))}
				/>
			{:else}
				<input
					{id}
					type="text"
					value={String(values[field.key] ?? '')}
					disabled={!writable || busy}
					onchange={(e) => save(scope, field.key, textValue(e))}
				/>
			{/if}
		</div>
	</div>
{/snippet}

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
						{@render row('profile', field, data.profile, true)}
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
					{@render row('host', field, data.host, data.host_writable)}
				{/each}
			</section>

			<section class="card">
				<h2>Node catalog</h2>
				<div class="setting inline">
					<div class="text">
						<span class="label">Refresh node catalog</span>
						<span class="help muted">
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
						</span>
					</div>
					<div class="control">
						<button type="button" class="btn" onclick={refreshCatalog} disabled={refreshing}>
							{refreshing ? 'Refreshing…' : 'Refresh'}
						</button>
					</div>
				</div>
			</section>

			<section class="card">
				<h2>Profile</h2>
				<div class="setting inline">
					<div class="text">
						<span class="label">Signed in as</span>
						<span class="help muted">
							{data.multi_user
								? 'Multi-user mode is on: each profile keeps its own inputs, history and outputs, protected by a password.'
								: 'Single-user mode: the Default profile is used without a password.'}
						</span>
					</div>
					<div class="control">
						<strong>{session.info?.profile?.name ?? 'Default'}</strong>
					</div>
				</div>
				{#if data.multi_user}
					<div class="setting inline">
						<div class="text">
							<span class="label">Switch profile</span>
							<span class="help muted">Sign out and choose another profile.</span>
						</div>
						<div class="control">
							<button type="button" class="btn" onclick={switchProfile} disabled={switching}>
								{switching ? 'Switching…' : 'Switch profile'}
							</button>
						</div>
					</div>
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

	.setting {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-3) 0;
		border-top: 1px solid var(--color-border);
	}
	.card h2 + .setting,
	.card .scope-note + .setting {
		border-top: 0;
	}
	.text {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		min-width: 0;
	}
	.label {
		font-weight: 600;
	}
	.help {
		font-size: var(--text-sm);
	}
	.control {
		min-width: 0;
	}
	/* Phone: a toggle keeps its row; other controls go under their label. */
	.setting.inline {
		flex-direction: row;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
	}
	.setting.inline .control {
		flex: none;
	}
	@media (min-width: 640px) {
		.setting {
			flex-direction: row;
			align-items: center;
			justify-content: space-between;
			gap: var(--space-4);
		}
		.setting .text {
			flex: 1 1 0;
		}
		.setting .control {
			flex: 0 1 18rem;
		}
		.setting.inline .control {
			flex: none;
		}
	}
	.error {
		color: var(--color-danger);
	}
</style>
