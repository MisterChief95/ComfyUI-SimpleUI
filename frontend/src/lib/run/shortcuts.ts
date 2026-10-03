/** Shared gate for run-page shortcuts; callers also suppress open dialogs/popovers. */
export function runShortcut(
	event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey' | 'repeat' | 'isComposing' | 'defaultPrevented'>,
	typing: boolean
): 'generate' | 'prompt' | 'seed' | null {
	if (event.defaultPrevented || event.repeat || event.isComposing) return null;
	if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.altKey) return 'generate';
	if (typing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return null;
	return event.key === 'p' ? 'prompt' : event.key === 'r' ? 'seed' : null;
}
