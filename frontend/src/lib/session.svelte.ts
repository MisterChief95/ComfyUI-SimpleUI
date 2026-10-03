// Shared reactive session state: who is signed in, and the profile picker
// list for the sign-in screen. One instance for the whole app.
import { api, setCsrfToken, describeApiError } from './api';
import { settingsState } from './settings.svelte';
import type { ProfileInfo, SessionInfo } from './contracts';

class SessionState {
	info = $state<SessionInfo | null>(null);
	profiles = $state<ProfileInfo[]>([]);
	loading = $state(true);
	error = $state<string | null>(null);

	async load(): Promise<void> {
		this.loading = true;
		this.error = null;
		try {
			this.info = await api<SessionInfo>('/session');
			setCsrfToken(this.info.csrf_token);
		} catch (cause) {
			this.error = describeApiError(cause);
		} finally {
			this.loading = false;
		}
	}

	/** Names only, for the sign-in picker; failure just leaves it empty. */
	async loadProfiles(): Promise<void> {
		try {
			const list = await api<{ profiles: ProfileInfo[] }>('/profiles');
			this.profiles = list.profiles;
		} catch {
			this.profiles = [];
		}
	}

	async login(name: string, password: string): Promise<void> {
		const info = await api<SessionInfo>('/session', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ name, password })
		});
		this._adopt(info);
	}

	/** Also used as "switch profile": the sign-in screen reappears after this. */
	async logout(): Promise<void> {
		const info = await api<SessionInfo>('/session', { method: 'DELETE' });
		this._adopt(info);
		this.profiles = [];
	}

	private _adopt(info: SessionInfo): void {
		this.info = info;
		setCsrfToken(info.csrf_token);
		// Sensitive per-profile state must not survive a profile switch.
		settingsState.reset();
	}
}

export const session = new SessionState();
