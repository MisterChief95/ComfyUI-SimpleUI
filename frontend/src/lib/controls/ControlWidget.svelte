<script lang="ts">
	// Value-only renderer for one control (run page and designer preview).
	// It owns no label/help/errors (see ControlRow.svelte) and no draft state:
	// the parent passes `value` and applies `onchange`.
	//
	// Emitted values (EditValue), matching POST /api/generations `edits`:
	//   checkbox        boolean
	//   int (any widget) ExactInt string, e.g. "42" -- never rounded through Number
	//   float           number (an empty/unparseable box emits its raw string, "")
	//   everything else string (select: option value; file: uploaded id, "" = none)
	import { onDestroy, onMount, tick } from 'svelte';
	import { api, describeApiError } from '$lib/api';
	import type { ControlDescriptor, EditValue } from '$lib/contracts';
	import { RANDOM_SEED, baseValue, sameValue, validateValue } from '$lib/run/values';
	import Icon from '$lib/ui/Icon.svelte';
	import SearchableSelect from '$lib/ui/SearchableSelect.svelte';
	import RecentPrompts from './RecentPrompts.svelte';
	import ModelInfo from './ModelInfo.svelte';
	import { registerPrompt } from './promptTarget';
	import { adjustWeight, completionAt, completionText, insertText, suggest } from './prompt';

	const MODEL_FOLDERS: Record<string, string> = {
		lora_name: 'loras',
		ckpt_name: 'checkpoints',
		vae_name: 'vae',
		control_net_name: 'controlnet'
	};

	let {
		control,
		value,
		onchange,
		disabled = false,
		compact = false,
		preview = false,
		describedby,
		id = control.binding_id,
		workflowId,
		lastSeed
	}: {
		control: ControlDescriptor;
		value: EditValue;
		onchange: (value: EditValue) => void;
		disabled?: boolean;
		/** Denser sizing (designer previews, tight grids). */
		compact?: boolean;
		/** Designer preview: file widgets show a local thumbnail only and never upload; selects have no search box. */
		preview?: boolean;
		/** Extra ids (help text, mapping errors) for aria-describedby on the input. */
		describedby?: string;
		/** DOM id of the focusable input, so a <label for> can target it. */
		id?: string;
		workflowId?: string;
		/** Seed controls: the previous run's seed (null = none yet). Omit to hide the "use last" button. */
		lastSeed?: string | null;
	} = $props();

	const text = $derived(value === null || value === undefined ? '' : String(value));
	let textarea = $state<HTMLTextAreaElement>();
	let selection = { start: 0, end: 0 };
	function rememberSelection(): void {
		selection = {
			start: textarea?.selectionStart ?? text.length,
			end: textarea?.selectionEnd ?? text.length
		};
	}
	async function insertPrompt(prompt: string): Promise<void> {
		onchange(insertText(text, prompt, selection.start, selection.end));
		await tick();
		textarea?.focus();
		textarea?.setSelectionRange(selection.start + prompt.length, selection.start + prompt.length);
	}

	// Prompt helpers: embedding:/<lora: autocomplete (names fetched once, lazily)
	// and Ctrl/Cmd+Up/Down weight editing.
	let namesRequested = false;
	let caret = $state(0);
	const completion = $derived(completionAt(text, caret));
	let pool = $state.raw<{ embeddings: string[]; loras: string[] }>({ embeddings: [], loras: [] });
	const choices = $derived(
		completion
			? suggest(completion.kind === 'embedding' ? pool.embeddings : pool.loras, completion.partial)
			: []
	);
	async function loadNames(): Promise<void> {
		if (namesRequested || preview) return;
		namesRequested = true;
		try {
			pool = await api('/catalog/completions');
		} catch {
			namesRequested = false; // retry on the next focus; typing works without suggestions
		}
	}
	function trackCaret(): void {
		caret = textarea?.selectionStart ?? 0;
	}
	onMount(() => {
		if (component !== 'textarea' || preview) return;
		// Trigger-word chips append at the caret (or the end if never focused).
		return registerPrompt((word) => {
			const at = textarea?.selectionStart ?? text.length;
			void applyEdit(insertAt(at, word));
		});
	});
	function insertAt(at: number, word: string): { value: string; start: number; end: number } {
		const end = at + word.length;
		return { value: insertText(text, word, at, at), start: end, end };
	}
	async function applyEdit(edit: { value: string; start: number; end: number }): Promise<void> {
		onchange(edit.value);
		await tick();
		textarea?.setSelectionRange(edit.start, edit.end);
		trackCaret();
	}
	function onPromptKey(e: KeyboardEvent): void {
		if (!(e.ctrlKey || e.metaKey) || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
		const edit = adjustWeight(
			text,
			textarea?.selectionStart ?? 0,
			textarea?.selectionEnd ?? 0,
			e.key === 'ArrowUp' ? 0.1 : -0.1
		);
		if (!edit) return;
		e.preventDefault();
		void applyEdit(edit);
	}
	function accept(name: string): void {
		if (!completion) return;
		const insert = completionText(completion.kind, name);
		const edit = {
			value: text.slice(0, completion.start) + insert + text.slice(caret),
			start: completion.start + insert.length,
			end: completion.start + insert.length
		};
		void applyEdit(edit).then(() => textarea?.focus());
	}
	const modelFolder = $derived(
		control.group === 'model' ? (MODEL_FOLDERS[control.input_name] ?? null) : null
	);
	const constraints = $derived(control.constraints);
	const isInt = $derived(control.logical_type === 'int');

	// A slider needs a finite range; an int slider additionally needs bounds that
	// are exact as JS numbers, otherwise fall back to the exact-text number box.
	const sliderUsable = $derived.by(() => {
		const c = constraints;
		if (!c || c.min === null || c.max === null || !(c.max > c.min)) return false;
		if (!isInt) return true;
		return (
			c.exact_min === null &&
			c.exact_max === null &&
			Number.isSafeInteger(c.min) &&
			Number.isSafeInteger(c.max)
		);
	});
	// A select with no catalog options (e.g. Ollama models, listed by ComfyUI's
	// own JS) becomes a text box so the value can still be typed.
	const component = $derived.by(() => {
		if (control.component === 'slider' && !sliderUsable) return 'number';
		if (control.component === 'select' && !control.options?.length) return 'text';
		return control.component;
	});

	// --- number / seed (ExactInt text) ---
	// An untouched control is never flagged (its imported value may be null/empty);
	// RunState runs the same validateValue on edited values to block Generate.
	const error = $derived(
		sameValue(control, value, baseValue(control)) ? null : validateValue(control, value)
	);
	const hintId = $derived(`${id}-hint`);
	const described = $derived(
		[describedby, error ? hintId : ''].filter(Boolean).join(' ') || undefined
	);
	const invalid = $derived(error ? true : undefined);

	// --- float / slider numeric parsing ---
	function emitNumber(el: HTMLInputElement): void {
		if (isInt) {
			// Keep typed integers exact, including invalid text for validation.
			onchange(el.value);
			return;
		}
		const n = el.valueAsNumber;
		if (Number.isNaN(n)) {
			onchange(el.value);
		} else {
			onchange(n);
		}
	}
	const sliderValue = $derived(Number(text));

	// --- textarea auto-grow ---
	// Attachment: re-runs when `text` changes; the observer regrows it when its
	// width changes or it becomes visible (e.g. inside a Sheet that just opened).
	function autogrow(el: HTMLTextAreaElement): () => void {
		void text;
		const grow = (): void => {
			el.style.height = 'auto';
			el.style.height = `${el.scrollHeight + (el.offsetHeight - el.clientHeight)}px`;
		};
		grow();
		let width = el.offsetWidth;
		const observer = new ResizeObserver(() => {
			if (el.offsetWidth !== width) {
				width = el.offsetWidth;
				grow();
			}
		});
		observer.observe(el);
		return () => observer.disconnect();
	}

	// --- select ---
	const options = $derived(control.options ?? []);
	// Option values are typed (1 is not "1"), so the <select> works on indexes.
	const selected = $derived(
		options.findIndex((o) => typeof o.value === typeof value && o.value === value)
	);

	// --- file ---
	let uploading = $state(false);
	let uploadError = $state<string | null>(null);
	let previewUrl = $state<string | null>(null);
	let fileName = $state('');
	// Aborted on destroy or when a newer pick supersedes it; a late completion is ignored.
	let pending: AbortController | null = null;
	const hasFile = $derived(preview ? previewUrl !== null : text !== '');

	function setPreview(file: File | null): void {
		if (previewUrl) URL.revokeObjectURL(previewUrl);
		previewUrl = file ? URL.createObjectURL(file) : null;
		fileName = file?.name ?? '';
	}
	onDestroy(() => {
		pending?.abort();
		setPreview(null);
	});

	// The one supported loader adapter binds both a reference image and a mask
	// through the "image" upload kind (app/mapping/input_adapters.py).
	async function upload(event: Event): Promise<void> {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		uploadError = null;
		if (preview) {
			setPreview(file);
			input.value = '';
			return;
		}
		pending?.abort();
		const request = (pending = new AbortController());
		uploading = true;
		try {
			const uploaded = await api<{ id: string }>(
				`/uploads?kind=image&filename=${encodeURIComponent(file.name)}`,
				{ method: 'POST', body: file, signal: request.signal }
			);
			if (request.signal.aborted) return;
			setPreview(file);
			onchange(uploaded.id);
		} catch (cause) {
			if (!request.signal.aborted) uploadError = describeApiError(cause);
		} finally {
			if (pending === request) {
				pending = null;
				uploading = false;
			}
			input.value = '';
		}
	}

	function clearFile(): void {
		pending?.abort();
		pending = null;
		uploading = false;
		setPreview(null);
		if (!preview) onchange('');
	}
</script>

<div class="widget" class:compact>
	{#if component === 'readonly'}
		<p class="readonly" {id} aria-describedby={described}>{text || '(preserved as imported)'}</p>
	{:else if component === 'textarea'}
		<textarea
			bind:this={textarea}
			{id}
			aria-describedby={described}
			{@attach autogrow}
			value={text}
			{disabled}
			rows="2"
			onfocus={() => {
				void loadNames();
				registerPrompt(
					(word) => void applyEdit(insertAt(textarea?.selectionStart ?? text.length, word)),
					true
				);
			}}
			onkeydown={onPromptKey}
			onkeyup={trackCaret}
			onclick={trackCaret}
			oninput={(e) => {
				onchange(e.currentTarget.value);
				trackCaret();
			}}></textarea>
		{#if choices.length}
			<ul class="suggest" aria-label="Suggestions">
				{#each choices as name (name)}
					<li><button type="button" class="btn" onclick={() => accept(name)}>{name}</button></li>
				{/each}
			</ul>
		{/if}
		{#if workflowId && !preview}
			<RecentPrompts
				{workflowId}
				bindingId={control.binding_id}
				{disabled}
				onopen={rememberSelection}
				oninsert={insertPrompt}
			/>
		{/if}
	{:else if component === 'checkbox'}
		<label class="toggle">
			<input
				{id}
				type="checkbox"
				aria-describedby={described}
				class="switch"
				role="switch"
				checked={value === true || value === 'true'}
				{disabled}
				onchange={(e) => onchange(e.currentTarget.checked)}
			/>
			<span class="muted">{value === true || value === 'true' ? 'On' : 'Off'}</span>
		</label>
	{:else if component === 'select'}
		<SearchableSelect
			{id}
			label={control.label}
			{options}
			{selected}
			missingText={text}
			{disabled}
			describedby={described}
			search={!preview}
			onchange={(index) => onchange(options[index].value)}
		/>
		{#if modelFolder && selected >= 0 && !preview}
			<ModelInfo folder={modelFolder} filename={String(options[selected].value)} />
		{/if}
	{:else if component === 'slider'}
		<div class="slider">
			<input
				{id}
				type="range"
				aria-describedby={described}
				aria-invalid={invalid}
				min={constraints?.min ?? undefined}
				max={constraints?.max ?? undefined}
				step={constraints?.step ?? (isInt ? 1 : 'any')}
				value={Number.isFinite(sliderValue) ? sliderValue : (constraints?.min ?? 0)}
				{disabled}
				oninput={(e) => emitNumber(e.currentTarget)}
			/>
			<input
				class="slider-number"
				type="number"
				aria-label={`${control.label} value`}
				aria-describedby={described}
				aria-invalid={invalid}
				min={constraints?.min ?? undefined}
				max={constraints?.max ?? undefined}
				step={constraints?.step ?? (isInt ? 1 : 'any')}
				value={text}
				{disabled}
				oninput={(e) => emitNumber(e.currentTarget)}
			/>
		</div>
	{:else if component === 'seed'}
		<div class="seed">
			<input
				{id}
				type="text"
				inputmode="numeric"
				autocomplete="off"
				spellcheck="false"
				value={text}
				{disabled}
				aria-describedby={described}
				aria-invalid={invalid}
				oninput={(e) => onchange(e.currentTarget.value)}
			/>
			<button
				type="button"
				class="btn btn-icon"
				{disabled}
				aria-label="Random seed"
				title="Random seed (-1)"
				onclick={() => onchange(RANDOM_SEED)}
			>
				<Icon name="dice" />
			</button>
			{#if lastSeed !== undefined}
				<button
					type="button"
					class="btn btn-icon"
					disabled={disabled || lastSeed === null}
					aria-label="Use last seed"
					title={lastSeed === null ? 'No previous seed yet' : 'Use last seed'}
					onclick={() => lastSeed !== null && onchange(lastSeed)}
				>
					<Icon name="recycle" />
				</button>
			{/if}
		</div>
	{:else if component === 'number'}
		{#if isInt}
			<input
				{id}
				type="text"
				inputmode="numeric"
				autocomplete="off"
				value={text}
				{disabled}
				aria-describedby={described}
				aria-invalid={invalid}
				oninput={(e) => onchange(e.currentTarget.value)}
			/>
		{:else}
			<input
				{id}
				type="number"
				aria-describedby={described}
				aria-invalid={invalid}
				min={constraints?.min ?? undefined}
				max={constraints?.max ?? undefined}
				step={constraints?.step ?? 'any'}
				value={text}
				{disabled}
				oninput={(e) => emitNumber(e.currentTarget)}
			/>
		{/if}
	{:else if component === 'file'}
		<div class="file">
			{#if previewUrl}
				<img class="thumb" src={previewUrl} alt={fileName || 'Selected image'} />
			{/if}
			<div class="file-body">
				<p class="current" title={text}>
					{#if uploading}Uploading…{:else if hasFile}{fileName || text}{:else}No file selected{/if}
				</p>
				<div class="row">
					<input
						{id}
						aria-describedby={described}
						type="file"
						accept="image/*"
						disabled={disabled || uploading}
						onchange={upload}
					/>
					{#if hasFile}
						<button
							type="button"
							class="btn btn-ghost btn-icon"
							{disabled}
							aria-label="Clear file"
							onclick={clearFile}
						>
							<Icon name="close" />
						</button>
					{/if}
				</div>
				{#if uploadError}<p class="hint err" role="alert">{uploadError}</p>{/if}
			</div>
		</div>
	{:else}
		<input
			{id}
			type="text"
			aria-describedby={described}
			value={text}
			{disabled}
			oninput={(e) => onchange(e.currentTarget.value)}
		/>
	{/if}
	{#if error}<p class="hint" id={hintId}>{error}</p>{/if}
</div>

<style>
	.widget {
		min-width: 0;
	}
	.widget.compact {
		--control-h: 2rem;
		--input-font: 0.875rem;
	}

	.suggest {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		margin: var(--space-1) 0 0;
		padding: 0;
		list-style: none;
	}
	textarea {
		overflow-y: auto;
		max-height: 50dvh;
		resize: none;
	}

	input[aria-invalid='true'] {
		border-color: var(--color-danger);
	}

	.readonly {
		margin: 0;
		padding: 0.4rem 0.625rem;
		min-height: var(--control-h);
		color: var(--color-text-muted);
		font-size: var(--text-sm);
		font-family: var(--font-mono);
		background: var(--color-surface-2);
		border: 1px dashed var(--color-border);
		border-radius: var(--radius);
		word-break: break-all;
	}

	.toggle {
		display: inline-flex;
		align-items: center;
		gap: 0.6rem;
		min-height: var(--control-h);
		cursor: pointer;
	}

	.slider {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
	.slider input[type='range'] {
		flex: 1 1 auto;
		min-width: 0;
	}
	.slider-number {
		flex: none;
		width: 5.5rem;
	}

	.seed {
		display: flex;
		gap: var(--space-2);
	}
	.seed input {
		flex: 1 1 auto;
		min-width: 0;
		font-family: var(--font-mono);
	}

	.file {
		display: flex;
		align-items: flex-start;
		gap: var(--space-2);
	}
	.thumb {
		flex: none;
		width: 4.5rem;
		height: 4.5rem;
		object-fit: cover;
		border-radius: var(--radius);
		border: 1px solid var(--color-border);
		background: var(--color-surface-2);
	}
	.file-body {
		flex: 1 1 auto;
		min-width: 0;
	}
	.current {
		margin: 0 0 var(--space-1);
		font-size: var(--text-sm);
		color: var(--color-text-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.hint {
		margin: var(--space-1) 0 0;
		font-size: var(--text-xs);
		color: var(--color-danger);
	}
</style>
