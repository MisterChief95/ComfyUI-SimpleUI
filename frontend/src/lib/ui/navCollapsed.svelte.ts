// Whether the desktop sidebar is collapsed to the icon rail. Per device, remembered in localStorage.
const KEY = 'simpleui:navCollapsed';

function read(): boolean {
	try {
		return localStorage.getItem(KEY) === '1';
	} catch {
		return false; // blocked storage: just no memory
	}
}

class NavCollapsed {
	value = $state(read());

	toggle(): void {
		this.value = !this.value;
		try {
			localStorage.setItem(KEY, this.value ? '1' : '0');
		} catch {
			// ignore
		}
	}
}

export const navCollapsed = new NavCollapsed();
