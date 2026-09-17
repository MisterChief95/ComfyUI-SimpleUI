<script lang="ts">
	import type { ControlDescriptor } from '$lib/contracts';
	import { ALLOWED_COMPONENTS } from '$lib/contracts';
	import { api, describeApiError } from '$lib/api';

	let {
		control,
		value = '',
		workflowId,
		onInput,
		onCorrected,
		mode = 'generate'
	}: {
		control: ControlDescriptor;
		value?: string;
		workflowId: string;
		onInput?: (bindingId: string, value: string) => void;
		onCorrected?: () => void;
		mode?: 'generate' | 'mapping';
	} = $props();

	let savingMapping = $state(false);
	let mappingError = $state<string | null>(null);
	let draftLabel = $state(control.label);
	let draftComponent = $state(control.component);

	// A display range/step/default only makes sense for a control with an
	// adjustable numeric range -- exact-transport ints (seeds) never get one.
	const isRangedNumber =
		(control.logical_type === 'int' || control.logical_type === 'float') &&
		control.constraints !== null &&
		control.constraints.exact_min === null &&
		control.constraints.exact_max === null;

	// Seeded from the *effective* (already-corrected) schema, so reopening the
	// editor shows the current saved override, not the original declared one.
	let draftDisplayMin = $state(String(control.constraints?.min ?? ''));
	let draftDisplayMax = $state(String(control.constraints?.max ?? ''));
	let draftDisplayStep = $state(String(control.constraints?.step ?? ''));
	let draftDisplayDefault = $state(isRangedNumber ? String(control.value ?? '') : '');

	let uploading = $state(false);
	let uploadError = $state<string | null>(null);

	function numberOrNull(text: string): number | null {
		const trimmed = text.trim();
		return trimmed === '' ? null : Number(trimmed);
	}

	function input(next: string): void {
		onInput?.(control.binding_id, next);
	}

	// The one supported loader adapter binds both a reference image and a
	// mask through the same "image" upload kind (app/mapping/input_adapters.py).
	async function uploadFile(event: Event): Promise<void> {
		const file = (event.currentTarget as HTMLInputElement).files?.[0];
		if (!file) return;
		uploading = true;
		uploadError = null;
		try {
			const uploaded = await api<{ id: string }>(
				`/uploads?kind=image&filename=${encodeURIComponent(file.name)}`,
				{ method: 'POST', body: file }
			);
			input(uploaded.id);
		} catch (cause) {
			uploadError = describeApiError(cause);
		} finally {
			uploading = false;
		}
	}

	async function saveMapping(): Promise<void> {
		savingMapping = true;
		mappingError = null;
		try {
			await api(`/workflows/${workflowId}/corrections`, {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					binding_id: control.binding_id,
					presentation: {
						label: draftLabel,
						component: draftComponent,
						group: null,
						order: null,
						help_text: null,
						display_min: isRangedNumber ? numberOrNull(draftDisplayMin) : null,
						display_max: isRangedNumber ? numberOrNull(draftDisplayMax) : null,
						display_default: isRangedNumber ? numberOrNull(draftDisplayDefault) : null,
						display_step: isRangedNumber ? numberOrNull(draftDisplayStep) : null
					}
				})
			});
			onCorrected?.();
		} catch (cause) {
			mappingError = describeApiError(cause);
		} finally {
			savingMapping = false;
		}
	}
</script>

<div class="field">
	<div class="field-header">
		<label for={mode === 'mapping' ? undefined : control.binding_id}>{control.label}</label>
	</div>

	{#if mode === 'mapping'}
		<div class="mapping-editor">
			<label>
				Label
				<input bind:value={draftLabel} maxlength="200" />
			</label>
			<label>
				Component
				<select bind:value={draftComponent}>
					{#each ALLOWED_COMPONENTS[control.logical_type] as option (option)}
						<option value={option}>{option}</option>
					{/each}
				</select>
			</label>
			{#if isRangedNumber}
				<label>
					Minimum
					<input type="number" bind:value={draftDisplayMin} />
				</label>
				<label>
					Maximum
					<input type="number" bind:value={draftDisplayMax} />
				</label>
				<label>
					Default
					<input type="number" bind:value={draftDisplayDefault} />
				</label>
				<label>
					Step
					<input type="number" min="0" step="any" bind:value={draftDisplayStep} />
				</label>
			{/if}
			{#if mappingError}<p class="error">{mappingError}</p>{/if}
			<button type="button" onclick={saveMapping} disabled={savingMapping}>
				{savingMapping ? 'Saving…' : 'Save'}
			</button>
		</div>
	{:else if control.component === 'readonly'}
		<p class="readonly-value">{value || '(preserved as imported)'}</p>
	{:else if control.component === 'textarea'}
		<textarea
			id={control.binding_id}
			{value}
			oninput={(e) => input(e.currentTarget.value)}
		></textarea>
	{:else if control.component === 'checkbox'}
		<input
			id={control.binding_id}
			type="checkbox"
			checked={value === 'true'}
			onchange={(e) => input(String(e.currentTarget.checked))}
		/>
	{:else if control.component === 'select'}
		<select id={control.binding_id} {value} onchange={(e) => input(e.currentTarget.value)}>
			{#each control.options ?? [] as option (option.value)}
				<option value={option.value} disabled={!option.available}>{option.label}</option>
			{/each}
		</select>
	{:else if control.component === 'slider'}
		<div class="slider-field">
			<input
				id={control.binding_id}
				type="range"
				min={control.constraints?.min ?? undefined}
				max={control.constraints?.max ?? undefined}
				step={control.constraints?.step ?? undefined}
				{value}
				oninput={(e) => input(e.currentTarget.value)}
			/>
			<input
				type="number"
				aria-label={`${control.label} value`}
				min={control.constraints?.min ?? undefined}
				max={control.constraints?.max ?? undefined}
				step={control.constraints?.step ?? undefined}
				{value}
				oninput={(e) => input(e.currentTarget.value)}
			/>
		</div>
	{:else if control.component === 'seed' || control.component === 'number'}
		<input
			id={control.binding_id}
			type="text"
			inputmode="numeric"
			{value}
			oninput={(e) => input(e.currentTarget.value)}
		/>
	{:else if control.component === 'file'}
		<div class="file-field">
			<p class="current-value">{value || '(none selected)'}</p>
			<input type="file" accept="image/*" onchange={uploadFile} disabled={uploading} />
			{#if uploading}<span>Uploading…</span>{/if}
			{#if uploadError}<p class="error">{uploadError}</p>{/if}
		</div>
	{:else}
		<input
			id={control.binding_id}
			type="text"
			{value}
			oninput={(e) => input(e.currentTarget.value)}
		/>
	{/if}

	{#if control.help_text}<p class="help">{control.help_text}</p>{/if}
	{#each control.unresolved as detail (detail.code + (detail.field ?? ''))}
		<p class="error">{detail.message}</p>
	{/each}
</div>

<style>
	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		padding: var(--space-2) 0;
		border-bottom: 1px solid var(--color-border);
	}

	.field-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
	}

	label {
		font-size: 0.875rem;
		font-weight: 600;
	}

	input[type='text'],
	input[type='range'],
	input[type='number'],
	select,
	textarea {
		min-height: var(--touch-target);
		padding: 0 var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-bg);
		width: 100%;
	}

	.slider-field {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.slider-field input[type='range'] {
		flex: 1;
	}

	.slider-field input[type='number'] {
		width: 6rem;
		flex: none;
	}

	textarea {
		min-height: 5rem;
		padding: var(--space-2) var(--space-3);
	}

	.mapping-editor {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-2);
		background: var(--color-bg-elevated);
		border-radius: var(--radius);
	}

	.mapping-editor label {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-weight: 400;
	}

	.readonly-value,
	.current-value {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.875rem;
		word-break: break-all;
	}

	.help {
		margin: 0;
		font-size: 0.75rem;
		color: var(--color-text-muted);
	}

	.error {
		margin: 0;
		color: var(--color-danger);
		font-size: 0.75rem;
	}
</style>
