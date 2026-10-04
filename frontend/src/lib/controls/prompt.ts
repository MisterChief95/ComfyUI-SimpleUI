/** Insert plain text, replacing the selected range without interpreting markup. */
export function insertText(value: string, text: string, start: number, end: number): string {
	return value.slice(0, start) + text + value.slice(end);
}

export interface Edit {
	value: string;
	start: number;
	end: number;
}

const NUM = String.raw`(\d+(?:\.\d+)?)`;
const WORD = /[^\s,()]/;

/**
 * Ctrl/Cmd+Up/Down weight edit in ComfyUI/A1111 `(term:1.1)` syntax, applied to
 * the selection or the word under the caret. Returns null if there is no target.
 * Weights step by 0.1; reaching 1.0 unwraps the term.
 */
export function adjustWeight(
	value: string,
	start: number,
	end: number,
	delta: number
): Edit | null {
	let s = start;
	let e = end;
	if (s === e) {
		while (s > 0 && WORD.test(value[s - 1])) s--;
		while (e < value.length && WORD.test(value[e])) e++;
		if (s === e) return null;
	}
	let region: [number, number] | null = null;
	let term = value.slice(s, e);
	let weight = 1;
	const whole = new RegExp(String.raw`^\(([\s\S]*):${NUM}\)$`).exec(term);
	const inner = new RegExp(String.raw`^([\s\S]+):${NUM}$`).exec(term);
	const tail = new RegExp(String.raw`^:${NUM}\)`).exec(value.slice(e));
	if (whole) {
		region = [s, e];
		term = whole[1];
		weight = Number(whole[2]);
	} else if (value[s - 1] === '(' && tail) {
		region = [s - 1, e + tail[0].length];
		weight = Number(tail[1]);
	} else if (inner && value[s - 1] === '(' && value[e] === ')') {
		region = [s - 1, e + 1];
		term = inner[1];
		weight = Number(inner[2]);
	}
	const next = Math.round((weight + delta) * 10) / 10;
	const [from, to] = region ?? [s, e];
	if (next < 0) return { value, start: from, end: to };
	const text = next === 1 ? term : `(${term}:${next.toFixed(1)})`;
	return {
		value: value.slice(0, from) + text + value.slice(to),
		start: from,
		end: from + text.length
	};
}

export interface Completion {
	kind: 'embedding' | 'lora';
	/** Offset where the typed token (including its `embedding:` / `<lora:` marker) begins. */
	start: number;
	partial: string;
}

/** The `embedding:xx` or `<lora:xx` token ending at the caret, if any. */
export function completionAt(value: string, caret: number): Completion | null {
	const m = /(?:^|[\s,(])((embedding:|<lora:)([^\s,<>():]*))$/i.exec(value.slice(0, caret));
	if (!m) return null;
	return {
		kind: m[2].toLowerCase() === 'embedding:' ? 'embedding' : 'lora',
		start: caret - m[1].length,
		partial: m[3]
	};
}

/** Case-insensitive substring matches, prefix matches first, capped. */
export function suggest(names: string[], partial: string, limit = 8): string[] {
	const p = partial.toLowerCase();
	const hits = names.filter((n) => n.toLowerCase().includes(p));
	hits.sort(
		(a, b) => Number(!a.toLowerCase().startsWith(p)) - Number(!b.toLowerCase().startsWith(p))
	);
	return hits.slice(0, limit);
}

/** The text a chosen suggestion becomes. LoRA names drop their extension and get a weight. */
export function completionText(kind: Completion['kind'], name: string): string {
	return kind === 'embedding' ? `embedding:${name}` : `<lora:${name.replace(/\.[^./\\]+$/, '')}:1>`;
}
