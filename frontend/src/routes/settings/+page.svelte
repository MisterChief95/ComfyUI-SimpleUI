<script lang="ts">
	import { onMount } from 'svelte';
	import { settingsState } from '$lib/settings.svelte';
	import { session } from '$lib/session.svelte';
	import { describeApiError } from '$lib/api';
	import type { SettingValue } from '$lib/contracts';

	// Mirrors backend/app/settings/service.py HOST_SETTINGS. Keep both in sync.
	const HOST_FIELDS = [
		{ key: 'comfy_url', label: 'ComfyUI URL', type: 'text' },
		{ key: 'comfy_input_dir', label: 'ComfyUI input folder', type: 'text' },
		{ key: 'comfy_output_dir', label: 'ComfyUI output folder', type: 'text' },
		{ key: 'upload_max_bytes', label: 'Upload size limit (bytes)', type: 'number' },
		{ key: 'pending_cap', label: 'Pending generation cap', type: 'number' },
		{ key: 'catalog_refresh_min_seconds', label: 'Catalog refresh cooldown (seconds)', type: 'number' },
		{ key: 'backup_dir', label: 'Backup folder', type: 'text' }
	] as const;

	// Mirrors backend/app/settings/service.py PROFILE_SETTINGS. Keep both in sync.
	const PROFILE_FIELDS = [
		{ key: 'theme', label: 'Theme', type: 'select', options: ['system', 'light', 'dark'] },
		{ key: 'density', label: 'Density', type: 'select', options: ['comfortable', 'compact'] },
		{ key: 'show_advanced', label: 'Show advanced controls', type: 'checkbox' },
		{ key: 'seed_policy', label: 'Seed policy', type: 'select', options: ['fixed', 'random', 'increment'] },
		{ key: 'live_previews', label: 'Live previews', type: 'checkbox' },
		{ key: 'video_enabled', label: 'Video generation', type: 'checkbox' },
		{ key: 'completion_sound', label: 'Completion sound', type: 'checkbox' },
		{ key: 'thumbnail_size', label: 'Thumbnail size', type: 'select', options: ['small', 'medium', 'large'] },
		{ key: 'gallery_autoplay', label: 'Autoplay videos in gallery', type: 'checkbox' },
		{ key: 'gallery_page_size', label: 'Gallery page size', type: 'number' },
		{ key: 'store_history', label: 'Store prompt/workflow history', type: 'checkbox' }
	] as const;

	onMount(() => {
		if (!settingsState.data) settingsState.load();
	});

	let savingKey = $state<string | null>(null);
	let saveError = $state<string | null>(null);

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

<section>
	<h1>Settings</h1>

	{#if settingsState.loading}
		<p>Loading…</p>
	{:else if settingsState.error}
		<p role="alert">{settingsState.error}</p>
	{:else if settingsState.data}
		{@const data = settingsState.data}

		<h2>Your profile</h2>
		<p class="scope-note">
			Applies only to {session.info?.profile?.name ?? 'your profile'}, on any device you sign in
			from.
		</p>
		<div class="fields">
			{#each PROFILE_FIELDS as field (field.key)}
				<label class="field" for={`profile-${field.key}`}>
					<span>{field.label}</span>
					{#if field.type === 'checkbox'}
						<input
							id={`profile-${field.key}`}
							type="checkbox"
							checked={Boolean(data.profile[field.key])}
							disabled={savingKey === field.key}
							onchange={(e) => save('profile', field.key, checkedValue(e))}
						/>
					{:else if field.type === 'select'}
						<select
							id={`profile-${field.key}`}
							value={String(data.profile[field.key] ?? field.options[0])}
							disabled={savingKey === field.key}
							onchange={(e) => save('profile', field.key, textValue(e))}
						>
							{#each field.options as option (option)}
								<option value={option}>{option}</option>
							{/each}
						</select>
					{:else}
						<input
							id={`profile-${field.key}`}
							type="number"
							min="0"
							value={Number(data.profile[field.key] ?? 0)}
							disabled={savingKey === field.key}
							onchange={(e) => save('profile', field.key, numberValue(e))}
						/>
					{/if}
				</label>
			{/each}
		</div>

		<h2>Local host</h2>
		<p class="scope-note">
			{#if data.host_writable}
				Applies to this whole installation and every profile on it.
			{:else}
				Read-only from here: this is a local host setting. Change it in the browser on the
				computer running SimpleUI (http://localhost), not from another device.
			{/if}
		</p>
		<div class="fields">
			{#each HOST_FIELDS as field (field.key)}
				<label class="field" for={`host-${field.key}`}>
					<span>{field.label}</span>
					{#if field.type === 'number'}
						<input
							id={`host-${field.key}`}
							type="number"
							min="0"
							value={Number(data.host[field.key] ?? 0)}
							disabled={!data.host_writable || savingKey === field.key}
							onchange={(e) => save('host', field.key, numberValue(e))}
						/>
					{:else}
						<input
							id={`host-${field.key}`}
							type="text"
							value={String(data.host[field.key] ?? '')}
							disabled={!data.host_writable || savingKey === field.key}
							onchange={(e) => save('host', field.key, textValue(e))}
						/>
					{/if}
				</label>
			{/each}
		</div>

		{#if saveError}
			<p class="error" role="alert">{saveError}</p>
		{/if}
	{/if}
</section>

<style>
	section {
		max-width: 40rem;
	}

	.scope-note {
		color: var(--color-text-muted);
		font-size: 0.875rem;
		margin-top: 0;
	}

	.fields {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin-bottom: var(--space-5);
	}

	.field {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
	}

	.field input[type='text'],
	.field input[type='number'],
	.field select {
		min-height: var(--touch-target);
		padding: 0 var(--space-2);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-bg);
		max-width: 14rem;
	}

	.field input[type='checkbox'] {
		width: 1.5rem;
		height: 1.5rem;
	}

	.error {
		color: var(--color-danger);
	}
</style>
