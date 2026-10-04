import { readFlag, writeFlag } from './storage';
// Whether the desktop sidebar is collapsed to the icon rail. Per device, remembered in localStorage.
const KEY = 'simpleui:navCollapsed';

class NavCollapsed {
	value = $state(readFlag(KEY));

	toggle(): void {
		this.value = !this.value;
		writeFlag(KEY, this.value);
	}
}

export const navCollapsed = new NavCollapsed();
