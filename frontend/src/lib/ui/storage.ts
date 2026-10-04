// Device storage is optional. Keep parsing/validation in the feature that owns the value.
export function readStored(key: string): string | null {
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}

export function writeStored(key: string, value: string): boolean {
	try {
		localStorage.setItem(key, value);
		return true;
	} catch {
		return false;
	}
}

export function removeStored(key: string): void {
	try {
		localStorage.removeItem(key);
	} catch {
		// In-memory state still works when storage is blocked.
	}
}

export function readJson<T>(key: string, fallback: T): T {
	try {
		const raw = readStored(key);
		return raw ? (JSON.parse(raw) as T) : fallback;
	} catch {
		return fallback;
	}
}

/** False when serialization or storage fails; drafts use this to report lost persistence. */
export function writeJson(key: string, value: unknown): boolean {
	try {
		return writeStored(key, JSON.stringify(value));
	} catch {
		return false;
	}
}

export function readFlag(key: string, fallback = false): boolean {
	const saved = readStored(key);
	return saved === null ? fallback : saved === '1';
}

export function writeFlag(key: string, value: boolean): void {
	writeStored(key, value ? '1' : '0');
}

export function readPanelWidth(key: string): number | null {
	const saved = Number(readStored(key));
	return Number.isFinite(saved) && saved > 0 ? saved : null;
}
