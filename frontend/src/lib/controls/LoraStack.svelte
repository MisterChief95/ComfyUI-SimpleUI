<script lang="ts">
	// Editor for the node pack's SimpleUILoraStack `loras` payload (PACK-002).
	// Trigger words are seeded from model metadata and shown read-only, with an
	// edit button for overrides; the payload is their only store. Emits the
	// whole payload as one string; unknown fields are preserved
	// (loraStack.ts). An unreadable payload shows why and stays editable as raw
	// text. A LoRA the catalog does not list is flagged, never dropped.
	import { api } from '$lib/api';
	import type { EnumOption, LoraColumns } from '$lib/contracts';
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
		describedby,
		showThumbnails = true,
		showClip = true,
		columns = 1
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
		/** Designer property: show entry thumbnails. */
		showThumbnails?: boolean;
		/** Designer property: off makes clip strength follow model strength. */
		showClip?: boolean;
		/**
		 * Designer property: LoRAs per row, a desktop hint like a section's
		 * columns. The stack's own width decides how many fit; phones show one.
		 */
		columns?: LoraColumns;
	} = $props();

	/** Slider travel; typed values may go past it. */
	const STRENGTH = { min: -2, max: 2, step: 0.05 };
	/** Entries whose trigger words are being edited, by index. */
	let editing = $state<Record<number, boolean>>({});

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
		editing = {};
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
		if (!Number.isFinite(n)) return;
		// With CLIP hidden, clip strength follows model strength.
		patch(index, showClip ? { [key]: n } : { strength_model: n, strength_clip: n });
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
		<!-- Reading order: entries fill each row left to right. -->
		<ol data-cols={columns}>
			{#each parsed.doc.loras as entry, i (i)}
				{@const catalogName = known.get(entry.name)}
				{@const info = catalogName ? infos[catalogName] : null}
				{@const suggestions = (info?.trigger_words ?? [])
					.filter((w) => !entry.trigger_words.includes(w))
					.slice(0, 6)}
				<li class="entry" class:off={!entry.enabled}>
					<div class="head">
						{#if showThumbnails}
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
						{/if}
						<div class="title">
							<span class="name" title={entry.name}>{entry.name}</span>
							{#if !catalogName}
								<span class="badge badge-danger">Not installed</span>
							{:else if info?.base_model}
								<span class="muted">{info.base_model}</span>
							{/if}
						</div>
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
								onclick={() => {
									editing = {};
									update((doc) => doc.loras.splice(i, 1));
								}}><Icon name="trash" /></button
							>
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
					{#snippet slider(key: 'strength_model' | 'strength_clip', label: string)}
						<div class="strength">
							<span class="muted">{label}</span>
							<input
								type="range"
								min={STRENGTH.min}
								max={STRENGTH.max}
								step={STRENGTH.step}
								aria-label={`${label} strength for ${entry.name}`}
								value={entry[key]}
								{disabled}
								oninput={(e) => strength(i, key, e.currentTarget)}
							/>
							<input
								class="strength-number"
								type="number"
								step={STRENGTH.step}
								aria-label={`${label} strength value for ${entry.name}`}
								value={entry[key]}
								{disabled}
								oninput={(e) => strength(i, key, e.currentTarget)}
							/>
						</div>
					{/snippet}
					<div class="strengths">
						{@render slider('strength_model', showClip ? 'Model' : 'Strength')}
						{#if showClip}{@render slider('strength_clip', 'CLIP')}{/if}
					</div>
					<div class="triggers">
						{#if editing[i]}
							<input
								type="text"
								placeholder="Trigger words"
								aria-label={`Trigger words for ${entry.name}`}
								value={entry.trigger_words}
								{disabled}
								oninput={(e) => patch(i, { trigger_words: e.currentTarget.value })}
								onkeydown={(e) => e.key === 'Enter' && (editing[i] = false)}
							/>
							<button type="button" class="btn" onclick={() => (editing[i] = false)}>Done</button>
						{:else}
							<p class="words" class:muted={!entry.trigger_words.trim()}>
								{entry.trigger_words.trim() || 'No trigger words'}
							</p>
							<button
								type="button"
								class="btn btn-ghost btn-icon"
								aria-label={`Edit trigger words for ${entry.name}`}
								title="Override trigger words"
								{disabled}
								onclick={() => (editing[i] = true)}><Icon name="edit" /></button
							>
						{/if}
						<button
							type="button"
							class="btn"
							title="Insert into prompt"
							disabled={disabled || preview || !entry.trigger_words.trim()}
							onclick={() => insertIntoPrompt(`${entry.trigger_words.trim()}, `)}>Insert</button
						>
					</div>
					{#if editing[i] && suggestions.length && !disabled}
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
	.lora-stack {
		container: lora-stack / inline-size;
	}
	ol,
	ul {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	ol {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		align-items: start;
		gap: var(--space-2);
	}
	/* Same breakpoints model as sections (SectionCard): one per row on phones,
	   then as many as the stack's own width fits, up to the designer's hint. */
	@media (min-width: 768px) {
		@container lora-stack (min-width: 34rem) {
			ol[data-cols='2'],
			ol[data-cols='3'] {
				grid-template-columns: repeat(2, minmax(0, 1fr));
			}
		}
		@container lora-stack (min-width: 52rem) {
			ol[data-cols='3'] {
				grid-template-columns: repeat(3, minmax(0, 1fr));
			}
		}
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
	.entry.off .strengths {
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
		display: grid;
		gap: var(--space-1);
	}
	.strength {
		display: grid;
		grid-template-columns: 4.5rem minmax(0, 1fr) 5rem;
		align-items: center;
		gap: var(--space-2);
		font-size: var(--text-sm);
	}
	.order {
		display: flex;
		flex: none;
	}
	.words {
		flex: 1 1 auto;
		min-width: 0;
		margin: 0;
		font-size: var(--text-sm);
		overflow-wrap: anywhere;
	}
	.triggers {
		display: flex;
		align-items: center;
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
