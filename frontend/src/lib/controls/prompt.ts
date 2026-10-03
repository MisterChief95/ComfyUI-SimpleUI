/** Insert plain text, replacing the selected range without interpreting markup. */
export function insertText(value: string, text: string, start: number, end: number): string {
	return value.slice(0, start) + text + value.slice(end);
}
