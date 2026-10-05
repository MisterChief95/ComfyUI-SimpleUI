<script lang="ts">
	// Editor for the node pack's SimpleUILoraStack `loras` payload (PACK-002).
	// Emits the whole payload as one string; unknown fields are preserved
	// (loraStack.ts). An unreadable payload shows why and stays editable as raw
	// text. A LoRA the catalog does not list is flagged, never dropped.
	import { api } from '$lib/api';
	import type { EnumOption } from '$lib/contracts';
	import Icon from '$lib/ui/Icon.svelte';
	import SearchableSelect from '$lib/ui/SearchableSelect.svelte';
	import { insertIntoPrompt } from './promptTarget';
	import {
		catalogIndex,
		newEntry,
		parseStack,
		serializeStack,
		type LoraDoc,
		type LoraEntry
	} from './loraStack';

	let {
		id,
		value,
		options,
		onchange,
		disabled = false,
		preview = false,
		describedby
	}: {
		id: string;
		value: string;
		/** The catalog's LoRA filenames, in catalog spelling. */
		options: EnumOption[];
		onchange: (value: string) => void;
		disabled?: boolean;
		/** Designer preview: no metadata or thumbnail requests. */
		preview?: boolean;
		describedby?: string;
	} = $props();

	type Info = { base_model: string | null; trigger_words: string[] };

	const parsed = $derived(parseStack(value));
	const catalogNames = $derived(options.map((o) => String(o.value)));
	const known = $derived(catalogIndex(catalogNames));

	// Metadata per catalog filename, shared by every stack on the page.
	let infos = $state<Record<string, Info | null>>({});
	let broken = $state<Record<string, boolean>>({});
	const requested = new Set<string>();

	function loadInfo(catalogName: string): Promise<Info | null> {
		requested.add(catalogName);
		return api<Info>(`/catalog/models/loras/info?filename=${encodeURIComponent(catalogName)}`)
			.then((info) => (infos[catalogName] = info))
			.catch(() => (infos[catalogName] = null));
	}

	$effect(() => {
		if (preview || !parsed.ok) return;
		for (const entry of parsed.doc.loras) {
			const name = known.get(entry.name);
			if (name && !requested.has(name)) void loadInfo(name);
		}
	});

	function update(change: (doc: LoraDoc) => void): void {
		if (!parsed.ok) return;
		const doc = structuredClone($state.snapshot(parsed.doc)) as LoraDoc;
		change(doc);
		onchange(serializeStack(doc));
	}

	function patch(index: number, fields: Partial<LoraEntry>): void {
		update((doc) => Object.assign(doc.loras[index], fields));
	}

	function move(index: number, by: number): void {
		update((doc) => {
			const [entry] = doc.loras.splice(index, 1);
			doc.loras.splice(index + by, 0, entry);
		});
	}

	async function add(index: number): Promise<void> {
		const catalogName = catalogNames[index];
		update((doc) => doc.loras.push(newEntry(catalogName)));
		if (preview) return;
		// Seed the new entry's trigger words from metadata, unless edited meanwhile.
		const info = infos[catalogName] ?? (await loadInfo(catalogName));
		const first = info?.trigger_words[0];
		if (!first) return;
		const current = parseStack(value);
		if (!current.ok) return;
		const at = current.doc.loras.findLastIndex(
			(e) => e.name === newEntry(catalogName).name && e.trigger_words === ''
		);
		if (at >= 0) patch(at, { trigger_words: first });
	}

	function appendWord(index: number, entry: LoraEntry, word: string): void {
		const words = entry.trigger_words.trim();
		patch(index, { trigger_words: words ? `${words}, ${word}` : word });
	}

	function strength(index: number, key: 'strength_model' | 'strength_clip', el: HTMLInputElement) {
		const n = el.valueAsNumber;
		if (Number.isFinite(n)) patch(index, { [key]: n });
	}
</script>

{#if !parsed.ok}
	<div class="stack-raw">
		<p class="diagnostic" role="alert">{parsed.problem} Edit the raw value below.</p>
		<textarea
			{id}
			aria-describedby={describedby}
			rows="4"
			{value}
			{disabled}
			oninput={(e) => onchange(e.currentTarget.value)}></textarea>
	</div>
{:else}
	<div class="lora-stack" {id} aria-describedby={describedby}>
		{#if parsed.doc.loras.length === 0}
			<p class="muted empty">No LoRAs. Add one below.</p>
		{/if}
		<ol>
			{#each parsed.doc.loras as entry, i (i)}
				{@const catalogName = known.get(entry.name)}
				{@const info = catalogName ? infos[catalogName] : null}
				{@const suggestions = (info?.trigger_words ?? [])
					.filter((w) => !entry.trigger_words.includes(w))
					.slice(0, 6)}
				<li class="entry" class:off={!entry.enabled}>
					<div class="head">
						{#if catalogName && !preview && !broken[catalogName]}
							<img
								src={`/api/catalog/models/loras/preview?filename=${encodeURIComponent(catalogName)}`}
								alt=""
								loading="lazy"
								onerror={() => (broken[catalogName] = true)}
							/>
						{:else}
							<div class="thumb" aria-hidden="true"></div>
						{/if}
						<div class="title">
							<span class="name" title={entry.name}>{entry.name}</span>
							{#if !catalogName}
								<span class="badge badge-danger">Not installed</span>
							{:else if info?.base_model}
								<span class="muted">{info.base_model}</span>
							{/if}
						</div>
						<label class="toggle" title={entry.enabled ? 'Enabled' : 'Disabled'}>
							<input
								type="checkbox"
								class="switch"
								role="switch"
								aria-label={`Enable ${entry.name}`}
								checked={entry.enabled}
								{disabled}
								onchange={(e) => patch(i, { enabled: e.currentTarget.checked })}
							/>
						</label>
					</div>
					<div class="strengths">
						<label>
							<span class="muted">Model</span>
							<input
								type="number"
								step="0.05"
								value={entry.strength_model}
								{disabled}
								oninput={(e) => strength(i, 'strength_model', e.currentTarget)}
							/>
						</label>
						<label>
							<span class="muted">CLIP</span>
							<input
								type="number"
								step="0.05"
								value={entry.strength_clip}
								{disabled}
								oninput={(e) => strength(i, 'strength_clip', e.currentTarget)}
							/>
						</label>
						<div class="order">
							<button
								type="button"
								class="btn btn-ghost btn-icon"
								aria-label={`Move ${entry.name} up`}
								disabled={disabled || i === 0}
								onclick={() => move(i, -1)}><Icon name="chevron-up" /></button
							>
							<button
								type="button"
								class="btn btn-ghost btn-icon"
								aria-label={`Move ${entry.name} down`}
								disabled={disabled || i === parsed.doc.loras.length - 1}
								onclick={() => move(i, 1)}><Icon name="chevron-down" /></button
							>
							<button
								type="button"
								class="btn btn-ghost btn-icon"
								aria-label={`Remove ${entry.name}`}
								{disabled}
								onclick={() => update((doc) => doc.loras.splice(i, 1))}
								><Icon name="trash" /></button
							>
						</div>
					</div>
					<div class="triggers">
						<input
							type="text"
							placeholder="Trigger words"
							aria-label={`Trigger words for ${entry.name}`}
							value={entry.trigger_words}
							{disabled}
							oninput={(e) => patch(i, { trigger_words: e.currentTarget.value })}
						/>
						<button
							type="button"
							class="btn"
							title="Insert into prompt"
							disabled={disabled || preview || !entry.trigger_words.trim()}
							onclick={() => insertIntoPrompt(`${entry.trigger_words.trim()}, `)}>Insert</button
						>
					</div>
					{#if suggestions.length && !disabled}
						<ul class="suggest" aria-label={`Suggested trigger words for ${entry.name}`}>
							{#each suggestions as word (word)}
								<li>
									<button
										type="button"
										class="chip"
										title="Add to trigger words"
										onclick={() => appendWord(i, entry, word)}>+ {word}</button
									>
								</li>
							{/each}
						</ul>
					{/if}
				</li>
			{/each}
		</ol>
		<div class="add">
			<!-- Re-created after each add so it returns to "Select…". -->
			{#key parsed.doc.loras.length}
				<SearchableSelect
					id={`${id}-add`}
					label="Add LoRA"
					{options}
					selected={-1}
					missingText=""
					disabled={disabled || options.length === 0}
					search={!preview}
					onchange={(index) => void add(index)}
				/>
			{/key}
			{#if options.length === 0}
				<p class="muted empty">ComfyUI lists no LoRAs.</p>
			{/if}
		</div>
	</div>
{/if}

<style>
	.lora-stack,
	.stack-raw {
		display: grid;
		gap: var(--space-2);
	}
	ol,
	ul {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	ol {
		display: grid;
		gap: var(--space-2);
	}
	.entry {
		display: grid;
		gap: var(--space-2);
		padding: var(--space-2);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-surface-2);
	}
	.entry.off .head,
	.entry.off .strengths label {
		opacity: 0.6;
	}
	.head {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
	img,
	.thumb {
		flex: none;
		width: 3rem;
		height: 3rem;
		object-fit: cover;
		border-radius: var(--radius-sm);
		background: var(--color-surface-3);
	}
	.title {
		display: grid;
		flex: 1 1 auto;
		min-width: 0;
		font-size: var(--text-sm);
		justify-items: start;
	}
	.name {
		max-width: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-weight: 600;
	}
	.toggle {
		flex: none;
		cursor: pointer;
	}
	.strengths {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}
	.strengths label {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		font-size: var(--text-sm);
	}
	.strengths input {
		width: 5rem;
	}
	.order {
		display: flex;
		margin-left: auto;
	}
	.triggers {
		display: flex;
		gap: var(--space-2);
	}
	.triggers input {
		flex: 1 1 auto;
		min-width: 0;
	}
	.suggest {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
	}
	.empty {
		margin: 0;
		font-size: var(--text-sm);
	}
	.diagnostic {
		margin: 0;
		font-size: var(--text-sm);
		color: var(--color-danger);
	}
	textarea {
		font-family: var(--font-mono);
		font-size: var(--text-sm);
	}
</style>
