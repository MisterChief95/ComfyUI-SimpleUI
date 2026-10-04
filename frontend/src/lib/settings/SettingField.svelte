<script lang="ts">
	import type { SettingValue } from '$lib/contracts';
	import type { Field } from './fields';
	import SettingRow from './SettingRow.svelte';
	let {
		id,
		field,
		value,
		disabled = false,
		onchange
	}: {
		id: string;
		field: Field;
		value: SettingValue | undefined;
		disabled?: boolean;
		onchange: (value: SettingValue) => void;
	} = $props();
</script>

<SettingRow for={id} label={field.label} help={field.help} inline={field.type === 'checkbox'}>
	{#if field.type === 'checkbox'}
		<input
			{id}
			type="checkbox"
			class="switch"
			role="switch"
			checked={Boolean(value)}
			{disabled}
			onchange={(event) => onchange(event.currentTarget.checked)}
		/>
	{:else if field.type === 'select'}
		<select
			{id}
			value={String(value ?? field.options[0])}
			{disabled}
			onchange={(event) => onchange(event.currentTarget.value)}
		>
			{#each field.options as option (option)}<option value={option}>{option}</option>{/each}
		</select>
	{:else if field.type === 'number'}
		<input
			{id}
			type="number"
			min="0"
			value={Number(value ?? 0)}
			{disabled}
			onchange={(event) => onchange(Number(event.currentTarget.value))}
		/>
	{:else}
		<input
			{id}
			type="text"
			value={String(value ?? '')}
			{disabled}
			onchange={(event) => onchange(event.currentTarget.value)}
		/>
	{/if}
</SettingRow>
