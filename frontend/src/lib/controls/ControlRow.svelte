<script lang="ts">
	// Label + widget + help + unresolved-mapping errors for one control, with an
	// optional "modified" dot and reset button. Layout only; value state lives in
	// the parent (draft store), which passes `modified` and `onreset`.
	import type { ControlDescriptor, EditValue } from '$lib/contracts';
	import ControlWidget from './ControlWidget.svelte';
	import Icon from '$lib/ui/Icon.svelte';

	let {
		control,
		value,
		onchange,
		disabled = false,
		compact = false,
		modified = false,
		preview = false,
		onreset,
		workflowId
	}: {
		control: ControlDescriptor;
		value: EditValue;
		onchange: (value: EditValue) => void;
		disabled?: boolean;
		compact?: boolean;
		/** Value differs from the imported one: shows a dot and the reset button. */
		modified?: boolean;
		/** Designer preview: file widgets never upload (see ControlWidget). */
		preview?: boolean;
		/** Reset to the imported value; the button shows only when `modified`. */
		onreset?: () => void;
		workflowId?: string;
	} = $props();

	// Stable ids derived from the binding so help and mapping errors can describe the input.
	const id = $derived(control.binding_id);
	const helpId = $derived(`${id}-help`);
	const errorId = (index: number): string => `${id}-err${index}`;
	const describedby = $derived(
		[control.help_text ? helpId : '', ...control.unresolved.map((_, i) => errorId(i))]
			.filter(Boolean)
			.join(' ') || undefined
	);
</script>

<div class="control-row">
	<div class="head">
		<label for={id}>{control.label}</label>
		{#if modified}
			<span class="dot" title="Modified" role="img" aria-label="Modified"></span>
			{#if onreset}
				<button
					type="button"
					class="btn btn-ghost btn-icon reset"
					aria-label={`Reset ${control.label}`}
					title="Reset to imported value"
					{disabled}
					onclick={onreset}
				>
					<Icon name="reset" size={16} />
				</button>
			{/if}
		{/if}
	</div>
	<ControlWidget
		{control}
		{value}
		{onchange}
		{disabled}
		{compact}
		{preview}
		{describedby}
		{id}
		{workflowId}
	/>
	{#if control.help_text}<p class="help" id={helpId}>{control.help_text}</p>{/if}
	{#each control.unresolved as detail, index (detail.code + (detail.field ?? ''))}
		<p class="error" id={errorId(index)} role="alert">{detail.message}</p>
	{/each}
</div>

<style>
	.control-row {
		min-width: 0;
	}
	.head {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-height: 1.5rem;
		margin-bottom: 0.2rem;
	}
	label {
		font-size: var(--text-sm);
		font-weight: 600;
		color: var(--color-text-muted);
		overflow-wrap: anywhere;
	}
	.dot {
		flex: none;
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
		background: var(--color-accent);
	}
	.reset {
		margin-left: auto;
	}
	@media (pointer: fine) {
		.reset {
			--control-h: 1.5rem;
		}
	}
	.help {
		margin: 0.25rem 0 0;
		font-size: var(--text-xs);
		color: var(--color-text-faint);
	}
	.error {
		margin: 0.25rem 0 0;
		font-size: var(--text-xs);
		color: var(--color-danger);
	}
</style>
