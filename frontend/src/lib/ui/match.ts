// Pure, dependency-free matching; node --test runs this erasable TypeScript directly.
/** Original indices, with substring matches before subsequences and stable order in each rank. */
export function matchOptions(labels: readonly string[], query: string): number[] {
	const needle = query.trim().toLowerCase();
	const substrings: number[] = [];
	const subsequences: number[] = [];
	labels.forEach((label, index) => {
		const text = label.toLowerCase();
		if (text.includes(needle)) {
			substrings.push(index);
		} else {
			let position = 0;
			for (const char of needle) {
				const found = text.indexOf(char, position);
				if (found < 0) return;
				position = found + char.length;
			}
			subsequences.push(index);
		}
	});
	return [...substrings, ...subsequences];
}
