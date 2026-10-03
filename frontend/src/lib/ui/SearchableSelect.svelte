<script lang="ts">
	// A <select> replacement for long option lists: one text field that shows the
	// current choice and, when focused or typed into, opens a filtered listbox
	// (WAI-ARIA combobox with list autocomplete). Short lists, and callers that
	// pass search={false}, get the plain native select. Options are addressed by
	// original index so typed values (1 vs "1") survive filtering.
	import type { EnumOption } from '$lib/contracts';
	import { matchOptions } from './match';

	let {
		id,
		label,
		options,
		selected,
		missingText,
		onchange,
		disabled = false,
		describedby,
		search = true
	}: {
		id: string;
		label: string;
		options: EnumOption[];
		selected: number;
		missingText: string;
		onchange: (index: number) => void;
		disabled?: boolean;
		describedby?: string;
		/** False renders the plain select regardless of option count. */
		search?: boolean;
	} = $props();

	const searchable = $derived(search && options.length >= 20);
	const placeholder = $derived(missingText === '' ? 'Select…' : `(missing) ${missingText}`);
	const shown = $derived(selected >= 0 && selected < options.length ? options[selected].label : '');

	let open = $state(false);
	let query = $state('');
	/** Position in `listed`, -1 for none. */
	let active = $state(-1);
	let up = $state(false);
	let input = $state<HTMLInputElement>();
	let list = $state<HTMLUListElement>();

	const listed = $derived(matchOptions(options.map((option) => option.label), query));
	const listId = $derived(`${id}-list`);
	const statusId = $derived(`${id}-results`);
	const optionId = (index: number): string => `${id}-opt-${index}`;
	const describedBy = $derived([describedby, statusId].filter(Boolean).join(' '));

	function show(): void {
		if (open || disabled) return;
		query = '';
		const rect = input?.getBoundingClientRect();
		// Open upward when the field sits low in the viewport (e.g. the last control).
		up = !!rect && innerHeight - rect.bottom < 260 && rect.top > innerHeight - rect.bottom;
		open = true;
		active = Math.max(0, listed.indexOf(selected));
		scrollActive();
	}

	function close(): void {
		open = false;
		query = '';
		active = -1;
	}

	function choose(index: number): void {
		if (!options[index]?.available) return;
		if (index !== selected) onchange(index);
		close();
	}

	function scrollActive(): void {
		queueMicrotask(() => {
			if (active >= 0) list?.querySelector(`#${CSS.escape(optionId(listed[active]))}`)?.scrollIntoView({ block: 'nearest' });
		});
	}

	function move(delta: number): void {
		if (listed.length === 0) return;
		active = Math.min(listed.length - 1, Math.max(0, active + delta));
		scrollActive();
	}

	function onkeydown(event: KeyboardEvent): void {
		switch (event.key) {
			case 'ArrowDown':
			case 'ArrowUp':
				event.preventDefault();
				if (!open) show();
				else move(event.key === 'ArrowDown' ? 1 : -1);
				break;
			case 'PageDown':
			case 'PageUp':
				if (!open) return;
				event.preventDefault();
				move(event.key === 'PageDown' ? 10 : -10);
				break;
			case 'Enter':
				if (!open) return;
				event.preventDefault();
				if (active >= 0) choose(listed[active]);
				break;
			case 'Escape':
				if (!open) return;
				event.preventDefault();
				event.stopPropagation();
				close();
				break;
			case 'Tab':
				close();
				break;
		}
	}

	function oninput(event: Event): void {
		query = (event.currentTarget as HTMLInputElement).value;
		if (!open) {
			show();
			query = (event.currentTarget as HTMLInputElement).value;
		}
		active = listed.length ? 0 : -1;
		if (list) list.scrollTop = 0;
	}
</script>

{#if searchable}
	<div class="combo" class:open>
		<input
			bind:this={input}
			{id}
			type="text"
			role="combobox"
			autocomplete="off"
			spellcheck="false"
			aria-autocomplete="list"
			aria-expanded={open}
			aria-controls={listId}
			aria-activedescendant={open && active >= 0 ? optionId(listed[active]) : undefined}
			aria-describedby={describedBy}
			placeholder={open ? 'Search…' : placeholder}
			value={open ? query : shown}
			{disabled}
			onfocus={show}
			onclick={show}
			onblur={close}
			{oninput}
			{onkeydown}
		/>
		<p class="sr-only" id={statusId} role="status">
			{#if open}{listed.length === 0 ? 'No results.' : `${listed.length} ${listed.length === 1 ? 'result' : 'results'}.`}{/if}
		</p>
		{#if open}
			<ul class="list" class:up id={listId} role="listbox" aria-label={label} bind:this={list}>
				{#each listed as index, position (index)}
					{@const option = options[index]}
					<!-- Keyboard is handled on the input (aria-activedescendant); mousedown keeps its focus. -->
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<li
						id={optionId(index)}
						role="option"
						aria-selected={index === selected}
						aria-disabled={!option.available}
						class:active={position === active}
						onmousedown={(e) => e.preventDefault()}
						onclick={() => choose(index)}
					>
						{option.label}
					</li>
				{:else}
					<li class="none" role="presentation">No matches</li>
				{/each}
			</ul>
		{/if}
	</div>
{:else}
	<select
		{id}
		aria-describedby={describedby}
		value={options.length === 0 ? '' : String(selected)}
		disabled={disabled || options.length === 0}
		onchange={(e) => onchange(Number(e.currentTarget.value))}
	>
		{#if options.length === 0}
			<option value="" disabled>No Options Present</option>
		{:else if selected < 0}
			<option value="-1" disabled>{placeholder}</option>
		{/if}
		{#each options as option, index (index)}
			<option value={String(index)} disabled={!option.available}>{option.label}</option>
		{/each}
	</select>
{/if}

<style>
	.combo {
		position: relative;
		min-width: 0;
	}
	/* Same chevron as the native select (tokens.css). */
	input {
		padding-right: 2rem;
		text-overflow: ellipsis;
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239aa2b1' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
		background-repeat: no-repeat;
		background-position: right 0.6rem center;
		cursor: pointer;
	}
	.open input {
		cursor: text;
	}
	.list {
		position: absolute;
		z-index: 30;
		top: calc(100% + 0.25rem);
		left: 0;
		right: 0;
		max-height: min(18rem, 50vh);
		overflow-y: auto;
		overscroll-behavior: contain;
		margin: 0;
		padding: 0.25rem;
		list-style: none;
		background: var(--color-bg-elevated);
		border: 1px solid var(--color-border-strong);
		border-radius: var(--radius);
		box-shadow: var(--shadow-2);
	}
	.list.up {
		top: auto;
		bottom: calc(100% + 0.25rem);
	}
	li {
		display: flex;
		align-items: center;
		min-height: var(--touch-target);
		padding: 0.25rem 0.625rem;
		border-radius: var(--radius-sm);
		overflow-wrap: anywhere;
		cursor: pointer;
	}
	li.active {
		background: var(--color-surface-3);
	}
	li[aria-selected='true'] {
		color: var(--color-accent);
		font-weight: 600;
	}
	li[aria-disabled='true'] {
		color: var(--color-text-faint);
		cursor: not-allowed;
	}
	li.none {
		color: var(--color-text-muted);
		cursor: default;
	}
	@media (hover: hover) {
		li:not(.none, [aria-disabled='true']):hover {
			background: var(--color-surface-3);
		}
	}
</style>
