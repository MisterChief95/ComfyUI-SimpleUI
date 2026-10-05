// The node pack's SimpleUILoraStack `loras` payload (contract 1, schema 1).
// Pure helpers, no Svelte, so `npm test` covers them. Entries and the document
// are kept as the parsed objects, so fields this app does not know survive an
// edit; only the named keys are ever written.

export const LORA_SCHEMA = 1;

export interface LoraEntry {
	name: string;
	strength_model: number;
	strength_clip: number;
	enabled: boolean;
	trigger_words: string;
	[key: string]: unknown;
}

export interface LoraDoc {
	schema: number;
	loras: LoraEntry[];
	[key: string]: unknown;
}

export type Parsed = { ok: true; doc: LoraDoc } | { ok: false; problem: string };

/** Read a payload; anything this build cannot edit safely is reported, not repaired. */
export function parseStack(text: string): Parsed {
	let data: unknown;
	try {
		data = JSON.parse(text);
	} catch {
		return { ok: false, problem: 'The payload is not valid JSON.' };
	}
	if (typeof data !== 'object' || data === null || Array.isArray(data))
		return { ok: false, problem: 'The payload is not a JSON object.' };
	const doc = data as Record<string, unknown>;
	if (doc.schema !== LORA_SCHEMA)
		return {
			ok: false,
			problem: `The payload uses schema ${JSON.stringify(doc.schema)}; this app reads schema ${LORA_SCHEMA}.`
		};
	if (!Array.isArray(doc.loras)) return { ok: false, problem: "The payload has no 'loras' list." };
	for (const [i, item] of doc.loras.entries()) {
		const e = item as Record<string, unknown>;
		if (typeof e !== 'object' || e === null || Array.isArray(e))
			return { ok: false, problem: `Entry ${i + 1} is not an object.` };
		if (typeof e.name !== 'string') return { ok: false, problem: `Entry ${i + 1} has no name.` };
		for (const key of ['strength_model', 'strength_clip'])
			if (typeof e[key] !== 'number' || !Number.isFinite(e[key]))
				return { ok: false, problem: `Entry ${i + 1} (${e.name}) has a non-numeric ${key}.` };
		if (typeof e.enabled !== 'boolean')
			return { ok: false, problem: `Entry ${i + 1} (${e.name}) has a non-boolean enabled.` };
		if (e.trigger_words !== undefined && typeof e.trigger_words !== 'string')
			return { ok: false, problem: `Entry ${i + 1} (${e.name}) has non-text trigger words.` };
	}
	const loras = (doc.loras as Record<string, unknown>[]).map((e) => ({
		...e,
		trigger_words: (e.trigger_words as string | undefined) ?? ''
	})) as LoraEntry[];
	return { ok: true, doc: { ...doc, schema: LORA_SCHEMA, loras } };
}

export function serializeStack(doc: LoraDoc): string {
	return JSON.stringify(doc);
}

/** Payload names use forward slashes; ComfyUI lists them with OS separators. */
export function payloadName(catalogName: string): string {
	return catalogName.replaceAll('\\', '/');
}

/** payload name -> catalog name, for the names the catalog offers. */
export function catalogIndex(catalogNames: string[]): Map<string, string> {
	return new Map(catalogNames.map((n) => [payloadName(n), n]));
}

export function newEntry(catalogName: string, triggerWords = ''): LoraEntry {
	return {
		name: payloadName(catalogName),
		strength_model: 1,
		strength_clip: 1,
		enabled: true,
		trigger_words: triggerWords
	};
}

/** Why the stack cannot be submitted, or null. Disabled missing LoRAs are allowed (the pack skips them). */
export function stackProblem(text: string, catalogNames: string[]): string | null {
	const parsed = parseStack(text);
	if (!parsed.ok) return parsed.problem;
	const known = catalogIndex(catalogNames);
	const missing = parsed.doc.loras.filter((e) => e.enabled && !known.has(e.name));
	return missing.length
		? `Not installed: ${missing.map((e) => e.name).join(', ')}. Disable or remove ${missing.length === 1 ? 'it' : 'them'}.`
		: null;
}
