// Shared reactive settings state: GET /api/settings once, PUT per-key writes.
// One instance for the whole app (a settings screen is never mounted twice).
import { api, describeApiError } from './api';
import type { EffectiveSettings, SettingValue } from './contracts';

class SettingsState {
	data = $state<EffectiveSettings | null>(null);
	loading = $state(false);
	error = $state<string | null>(null);

	async load(): Promise<void> {
		this.loading = true;
		this.error = null;
		try {
			this.data = await api<EffectiveSettings>('/settings');
		} catch (cause) {
			this.error = describeApiError(cause);
		} finally {
			this.loading = false;
		}
	}

	/** Owner comes from the session; a key rejected by the backend rolls back. */
	async setProfile(key: string, value: SettingValue): Promise<void> {
		await this._write('profile', key, value);
	}

	/** Backend also enforces the local-only gate; this can still 403 remotely. */
	async setHost(key: string, value: SettingValue): Promise<void> {
		await this._write('host', key, value);
	}

	private async _write(scope: 'profile' | 'host', key: string, value: SettingValue): Promise<void> {
		if (!this.data) return;
		const previous = this.data[scope][key];
		this.data[scope][key] = value;
		try {
			await api(`/settings/${scope}`, {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ key, value })
			});
		} catch (cause) {
			this.data[scope][key] = previous;
			throw cause;
		}
	}

	/** Called on sign-out/profile switch so no profile's values linger in memory. */
	reset(): void {
		this.data = null;
		this.error = null;
	}
}

export const settingsState = new SettingsState();
