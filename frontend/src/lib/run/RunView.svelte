<script lang="ts">
	// The run page for one workflow: header, user-designed sections, result
	// panel, and the generate bar. Phone: one scroll area (result above the
	// controls) and the bar pinned at the bottom; tablet/desktop: controls and
	// result as two self-scrolling columns.
	import { onDestroy, onMount, tick } from 'svelte';
	import { fade, slide } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { goto } from '$app/navigation';
	import Icon from '$lib/ui/Icon.svelte';
	import ResizeHandle from '$lib/ui/ResizeHandle.svelte';
	import { readPanelWidth } from '$lib/ui/storage';
	import { isolatePopoverInput } from '$lib/ui/isolateInput';
	import GenerateBar from './GenerateBar.svelte';
	import PresetsMenu from './PresetsMenu.svelte';
	import StylesMenu from './StylesMenu.svelte';
	import QueuePanel from './QueuePanel.svelte';
	import ResultPanel from './ResultPanel.svelte';
	import SectionCard from './SectionCard.svelte';
	import { RunState } from './run.svelte';
	import { statusInfo } from './status';
	import { groupWarnings } from './warnings';
	import { runShortcut } from './shortcuts';
	import { RANDOM_SEED } from './values';

	let { workflowId }: { workflowId: string } = $props();

	// The route wraps this component in {#key}, so the id never changes while mounted.
	// svelte-ignore state_referenced_locally
	const run = new RunState(workflowId);
	onMount(() => void run.load());
	onDestroy(() => run.dispose());

	const info = $derived(statusInfo(run.tracker.latest));
	let toolbarH = $state(0);
	// Result column width in px (tablet/desktop); null = the 40% default. Per-device preference.
	const WIDTH_KEY = 'simpleui.resultWidth';
	let body = $state<HTMLElement>();
	let resultW = $state<number | null>(readPanelWidth(WIDTH_KEY));

	let presetsOpen = $state(false);
	let stylesOpen = $state(false);
	let queueOpen = $state(false);
	const blocking = $derived(run.schema?.blocking ?? []);
	const groups = $derived(groupWarnings(run.schema?.warnings ?? []));
	const warnings = $derived(groups.actionable);
	let warningsOpen = $state(false);
	const shortcutsId = $props.id();
	const warningsId = `${shortcutsId}-warnings`;
	let shortcutsEnabled = $state(true);
	const noticeCount = $derived(warnings.length + groups.info.length + groups.stale.length);

	// Go-to-top: the phone scrolls .body, tablet/desktop scroll .controls. Shown
	// once the user is roughly a screen away from the top.
	let scroller = $state<HTMLElement | null>(null);
	let scrolledDown = $state(false);
	function onscroll(event: Event): void {
		const el = event.currentTarget as HTMLElement;
		if (el.scrollHeight <= el.clientHeight) return;
		scroller = el;
		scrolledDown = el.scrollTop > Math.max(400, el.clientHeight * 0.75);
	}
	function toTop(): void {
		scroller?.scrollTo({ top: 0, behavior: prefersReducedMotion.current ? 'auto' : 'smooth' });
	}

	function onkeydown(event: KeyboardEvent): void {
		if (event.defaultPrevented || document.querySelector('dialog[open], :popover-open')) return;
		const target = event.target;
		const typing =
			target instanceof Element &&
			(target.closest('input, textarea, select, [role="textbox"]') !== null ||
				(target instanceof HTMLElement && target.isContentEditable));
		const action = runShortcut(event, typing);
		if (!action || (action !== 'generate' && !shortcutsEnabled)) return;
		event.preventDefault();
		if (action === 'generate') void run.submit();
		else if (action === 'prompt') void focusPrompt();
		else {
			const seed = run.sections
				.flatMap((section) => section.entries)
				.find(({ control }) => control.component === 'seed')?.control;
			if (seed) run.setValue(seed, RANDOM_SEED);
		}
	}

	async function focusPrompt(): Promise<void> {
		const candidates = run.sections.flatMap((section) =>
			section.entries
				.filter(({ control }) => control.component === 'textarea')
				.map(({ control }) => ({ section, control }))
		);
		const first =
			candidates.find(({ control }) => /prompt|text/i.test(control.label)) ?? candidates[0];
		if (!first) return;
		run.setOpen(first.section, true);
		await tick();
		document.getElementById(first.control.binding_id)?.focus();
	}

	function jump(id: string): void {
		const section = run.sections.find((s) => s.id === id);
		if (section) run.setOpen(section, true);
		// Wait a frame so a just-opened section has its height before scrolling.
		requestAnimationFrame(() =>
			document.getElementById(`sec-${id}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' })
		);
	}
</script>

<svelte:window {onkeydown} />
<svelte:head><title>{run.name ?? 'Generate'} · SimpleUI</title></svelte:head>

<div class="page-full run">
	<header class="top">
		<label class="switcher">
			<span class="workspace-label">Generation / Workflow</span>
			<select
				aria-label="Workflow"
				value={workflowId}
				onchange={(e) => goto(`/generation/${e.currentTarget.value}`)}
			>
				{#each run.workflows as workflow (workflow.id)}
					<option value={workflow.id}>{workflow.name}</option>
				{:else}
					<option value={workflowId}>{run.name ?? 'Workflow'}</option>
				{/each}
			</select>
		</label>
		<a
			class="btn btn-ghost"
			href={`/workflows/${workflowId}`}
			aria-label="Open designer"
			title="Design"><Icon name="design" size={18} /> <span class="lbl">Design</span></a
		>
		<button
			type="button"
			class="btn btn-ghost"
			aria-label="Presets"
			title="Presets"
			disabled={run.loading || run.schema === null}
			onclick={() => {
				presetsOpen = true;
				void run.presets.load();
			}}
		>
			<Icon name="bookmark" size={18} /> <span class="lbl">Presets</span>
		</button>
		<button
			type="button"
			class="btn btn-ghost"
			aria-label={run.styleIds.length
				? `Prompt styles (${run.styleIds.length} selected)`
				: 'Prompt styles'}
			title="Prompt styles"
			disabled={run.loading || run.schema === null}
			onclick={() => (stylesOpen = true)}
		>
			<Icon name="sparkles" size={18} />
			<span class="lbl">Styles</span>
			{#if run.styleIds.length}<span class="count" aria-hidden="true">{run.styleIds.length}</span
				>{/if}
		</button>
		<button
			type="button"
			class="btn btn-ghost"
			aria-label="Queue"
			title="Queue"
			onclick={() => (queueOpen = true)}
		>
			<Icon name="list" size={18} /> <span class="lbl">Queue</span>
		</button>
		<button
			type="button"
			class="btn btn-ghost btn-icon kbd-help"
			title="Keyboard shortcuts"
			popovertarget={shortcutsId}
			aria-label="Keyboard shortcuts">?</button
		>
		<div id={shortcutsId} popover class="shortcuts" {@attach isolatePopoverInput}>
			<strong>Keyboard shortcuts</strong>
			<p><kbd>Ctrl/Cmd + Enter</kbd> Generate</p>
			<p><kbd>P</kbd> Focus first prompt</p>
			<p><kbd>R</kbd> Set first seed to random (-1)</p>
			<label
				><input type="checkbox" bind:checked={shortcutsEnabled} /> Enable P and R shortcuts</label
			>
			<p class="muted">
				P and R apply outside inputs. Shortcuts pause during composition and while dialogs or
				popovers are open.
			</p>
		</div>
		<span class={`badge badge-${info.kind} status`} role="status">{info.label}</span>
	</header>

	{#if run.loading}
		<p class="state muted">Loading workflow…</p>
	{:else if run.notFound}
		<div class="state">
			<p>This workflow no longer exists.</p>
			<a class="btn" href="/generation">Choose another workflow</a>
		</div>
	{:else if run.loadError}
		<div class="state">
			<p class="err" role="alert">{run.loadError}</p>
			<button type="button" class="btn" onclick={() => run.load()}>Retry</button>
		</div>
	{:else}
		<div
			class="body"
			bind:this={body}
			{onscroll}
			style:--result-w={resultW ? `${resultW}px` : '40%'}
		>
			<div class="controls" {onscroll} style:--toolbar-h={`${toolbarH}px`}>
				{#if run.mediaInput}
					<div class="notice row">
						<img
							class="media-input-preview"
							src={`/api/media/${run.mediaInput.id}/thumbnail`}
							alt="Selected workflow input"
						/>
						<span class="grow"
							>Selected for {run.schema?.controls.find(
								(c) => c.binding_id === run.mediaInput?.binding_id
							)?.label}</span
						>
						<button class="btn" type="button" onclick={() => (run.mediaInput = null)}>Clear</button>
					</div>
				{/if}
				{#if blocking.length > 0}
					<div class="notice err-box" role="alert">
						<strong>This workflow cannot be submitted yet</strong>
						{#each blocking as detail (detail.code + (detail.field ?? ''))}<p>
								{detail.message}
							</p>{/each}
					</div>
				{/if}
				<div class="toolbar" bind:clientHeight={toolbarH}>
					<div class="chips">
						{#if noticeCount > 0}
							<button
								type="button"
								class="chip notes"
								class:warn={warnings.length + groups.stale.length > 0}
								aria-expanded={warningsOpen}
								aria-controls={warningsId}
								onclick={() => (warningsOpen = !warningsOpen)}
							>
								<Icon name={warnings.length ? 'alert' : 'info'} size={14} />
								{#if warnings.length > 0}
									{warnings.length} warning{warnings.length === 1 ? '' : 's'}
								{:else if groups.stale.length > 0}
									{groups.stale.length} stale
								{:else}
									Notes ({groups.info.length})
								{/if}
							</button>
						{/if}
						<button
							type="button"
							class="chip"
							aria-pressed={run.modifiedOnly}
							disabled={run.modifiedCount === 0 && !run.modifiedOnly}
							onclick={() => (run.modifiedOnly = !run.modifiedOnly)}
						>
							Modified{run.modifiedCount ? ` (${run.modifiedCount})` : ' only'}
						</button>
						{#if run.modifiedCount}
							<button type="button" class="chip" onclick={() => run.resetAll()}>Reset all</button>
						{/if}
						{#if run.sections.length > 3}
							<span class="sep" aria-hidden="true"></span>
							<nav class="jump" aria-label="Jump to section">
								{#each run.sections as section (section.id)}
									<button type="button" class="chip" onclick={() => jump(section.id)}
										>{section.title}</button
									>
								{/each}
							</nav>
						{/if}
					</div>
					{#if warningsOpen && noticeCount > 0}
						<div
							class="notes-panel"
							id={warningsId}
							transition:slide={{
								duration: prefersReducedMotion.current ? 0 : 180,
								easing: cubicOut
							}}
						>
							{#if groups.stale.length > 0}
								<p>
									{groups.stale.length} old saved correction{groups.stale.length === 1 ? '' : 's'}
									for controls that were removed or rewired {groups.stale.length === 1
										? 'is'
										: 'are'}
									being ignored.
									<button
										type="button"
										class="link"
										onclick={() =>
											void run.removeStaleCorrections(groups.stale.map((d) => d.field ?? ''))}
										>Remove {groups.stale.length === 1 ? 'it' : 'them'}</button
									>
									(your other corrections are kept).
								</p>
							{/if}
							{#each warnings as detail (detail.code + (detail.field ?? ''))}<p>
									{detail.message}
								</p>{/each}
							{#each groups.info as detail (detail.code + (detail.field ?? ''))}<p class="info">
									{detail.message}
								</p>{/each}
						</div>
					{/if}
					{#if run.presets.message}
						<p class="draft preset-msg" role="status">
							{run.presets.message}
							<button type="button" class="link" onclick={() => (run.presets.message = null)}
								>Dismiss</button
							>
						</p>
					{/if}
					{#if run.modifiedCount > 0}
						<p class="draft" class:warn={!run.draftPersisted} role="status">
							{run.draftPersisted
								? 'Draft saved on this device'
								: "Couldn't save draft on this device — it will be lost on reload"}
						</p>
					{/if}
				</div>

				{#each run.sections as section (section.id)}
					<SectionCard {run} {section} />
				{:else}
					<p class="muted empty">
						{#if run.modifiedOnly}
							No modified controls.
						{:else}
							This workflow has no visible controls. Place some in the <a
								href={`/workflows/${workflowId}`}>designer</a
							>.
						{/if}
					</p>
				{/each}
				{#if scrolledDown}
					<div class="to-top-dock">
						<button
							type="button"
							class="btn btn-icon to-top"
							aria-label="Back to top"
							title="Back to top"
							onclick={toTop}
							transition:fade={{ duration: prefersReducedMotion.current ? 0 : 150 }}
						>
							<Icon name="arrow-up" size={20} />
						</button>
					</div>
				{/if}
			</div>

			<ResizeHandle
				class="divider"
				bind:width={resultW}
				container={body}
				label="Resize result panel"
				storageKey={WIDTH_KEY}
				min={260}
				remaining={360}
				defaultWidth={Math.round((body?.clientWidth ?? 0) * 0.4)}
				extremes
			/>

			<aside class="result" aria-label="Result">
				<ResultPanel {run} />
			</aside>
		</div>
		<GenerateBar {run} />
		<PresetsMenu {run} bind:open={presetsOpen} />
		<StylesMenu {run} bind:open={stylesOpen} />
		{#if queueOpen}<QueuePanel {run} onclose={() => (queueOpen = false)} />{/if}
	{/if}
</div>

<style>
	/* Breakpoints: phone < 768px (one scroll area), tablet 768-1023px (two
	   columns, flat sections), desktop >= 1024px (two columns, section cards).
	   Below 1024px everything in the controls column is edge to edge: flat
	   banners and stacked accordions separated by 1px rules, no card margins. */
	.run {
		min-width: 0;
		--space-3: 0.75rem;
	}
	.top {
		flex: none;
		display: flex;
		flex-wrap: nowrap;
		align-items: center;
		gap: var(--space-1);
		padding: var(--space-1) var(--page-pad);
		border-bottom: 1px solid var(--color-border);
		background: var(--color-surface-1);
	}
	.switcher {
		flex: 1 1 0;
		min-width: 6rem;
		max-width: 24rem;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.workspace-label {
		padding-inline: var(--space-2);
		font-size: 0.625rem;
		font-weight: 650;
		letter-spacing: 0.02em;
		color: var(--color-text-faint);
	}
	.switcher select {
		font-weight: 650;
		background-color: transparent;
		border-color: transparent;
		padding-left: var(--space-2);
		text-overflow: ellipsis;
	}
	.switcher select:focus-visible {
		background-color: var(--color-surface-2);
	}
	.status {
		margin-left: auto;
		flex: none;
	}
	.top .btn {
		flex: none;
		position: relative;
	}
	.count {
		min-width: 1.1rem;
		padding: 0 0.3rem;
		font-size: var(--text-xs);
		line-height: 1.1rem;
		border-radius: var(--radius-full);
		color: var(--color-accent-text);
		background: var(--color-accent);
	}
	@media (pointer: coarse) {
		/* Keyboard shortcuts mean nothing on a touch screen. */
		.kbd-help {
			display: none;
		}
	}
	.shortcuts {
		max-width: min(24rem, calc(100vw - 2rem));
		padding: var(--space-3);
		color: var(--color-text);
		background: var(--color-surface-1);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		font-size: var(--text-sm);
	}
	.preset-msg {
		color: var(--color-text-muted);
	}
	@media (max-width: 1023px) {
		.workspace-label {
			display: none;
		}
	}
	@media (max-width: 767px) {
		.top {
			padding-right: max(var(--space-1), env(safe-area-inset-right));
			gap: 0;
		}
		.switcher select {
			padding-left: 0;
		}
		/* Icon-only actions; the styles count rides on the icon. */
		.top .btn {
			width: 2.5rem;
			padding: 0;
		}
		.lbl {
			display: none;
		}
		.count {
			position: absolute;
			top: 0.2rem;
			right: 0.1rem;
			min-width: 1rem;
			line-height: 1rem;
			font-size: 0.625rem;
		}
		.status {
			margin-left: var(--space-1);
			font-size: 0.6875rem;
		}
	}
	.state {
		padding: var(--page-pad);
	}
	.err {
		color: var(--color-danger);
	}

	/* Phone: the body is the single scroll area, result first, edge to edge. */
	.body {
		flex: 1 1 auto;
		min-height: 0;
		overflow-y: auto;
		scrollbar-gutter: stable;
		overscroll-behavior: contain;
		display: flex;
		flex-direction: column;
		padding: 0;
		gap: 0;
	}
	.body > :global(.divider) {
		display: none;
	}
	.result {
		order: -1;
		flex: none;
		padding: var(--space-2) var(--page-pad);
		background: var(--color-surface-1);
		border-bottom: 1px solid var(--color-border);
	}
	.controls {
		container: run / inline-size;
		display: flex;
		flex-direction: column;
		gap: 0;
		min-width: 0;
		/* Room for the go-to-top button below the last section. */
		padding-bottom: 4rem;
	}

	.toolbar {
		position: sticky;
		top: 0;
		z-index: 2;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-1) var(--space-2);
		padding: var(--space-1) var(--page-pad);
		background: var(--color-bg);
		border-bottom: 1px solid var(--color-border);
	}
	/* Filters, the warnings toggle and the section jumps share one scrolling row. */
	.chips {
		flex: 1 1 100%;
		min-width: 0;
		display: flex;
		align-items: center;
		gap: var(--space-1);
		overflow-x: auto;
		scrollbar-width: none;
		/* The coarse-pointer chip tap area reaches past the chip; don't clip it. */
		padding-block: 0.4rem;
		margin-block: -0.4rem;
	}
	.chips .chip {
		flex: none;
	}
	.jump {
		display: flex;
		gap: var(--space-1);
	}
	.sep {
		flex: none;
		width: 1px;
		height: 1.25rem;
		margin-inline: var(--space-1);
		background: var(--color-border);
	}
	.notes.warn {
		color: var(--color-warning);
		background: var(--color-warning-soft);
	}
	.notes-panel {
		flex-basis: 100%;
		max-height: 40dvh;
		overflow-y: auto;
		overscroll-behavior: contain;
		font-size: var(--text-sm);
	}
	.notes-panel p {
		margin: 0 0 var(--space-1);
	}
	.notes-panel .info {
		opacity: 0.75;
	}
	.draft {
		flex-basis: 100%;
		margin: 0;
		font-size: var(--text-xs);
		color: var(--color-text-faint);
	}
	.draft.warn {
		color: var(--color-danger);
		font-weight: 600;
	}
	.notice {
		padding: var(--space-2) var(--page-pad);
		font-size: var(--text-sm);
		background: var(--color-surface-1);
		border-bottom: 1px solid var(--color-border);
	}
	.notice p {
		margin: var(--space-1) 0 0;
	}
	.media-input-preview {
		width: 2.5rem;
		height: 2.5rem;
		object-fit: cover;
		border-radius: var(--radius-sm);
	}
	.link {
		padding: 0;
		margin-left: var(--space-1);
		border: 0;
		background: transparent;
		color: var(--color-accent, inherit);
		font: inherit;
		text-decoration: underline;
		cursor: pointer;
	}
	.err-box {
		color: var(--color-danger);
		background: var(--color-danger-soft);
	}
	.empty {
		padding: var(--space-3) var(--page-pad);
	}

	/* Go-to-top: a zero-height sticky dock at the end of the controls keeps the
	   button pinned to the bottom of whichever element scrolls them. */
	.to-top-dock {
		position: sticky;
		bottom: 0;
		z-index: 3;
		height: 0;
		pointer-events: none;
	}
	.to-top {
		position: absolute;
		right: max(var(--page-pad), env(safe-area-inset-right));
		bottom: var(--space-3);
		width: 2.75rem;
		max-width: none;
		min-height: 2.75rem;
		border-radius: 50%;
		pointer-events: auto;
		color: var(--color-accent-text);
		background: var(--color-accent);
		border-color: transparent;
		box-shadow: var(--shadow-2);
		opacity: 0.92;
	}
	.to-top:hover:not(:disabled) {
		background: var(--color-accent-hover);
		border-color: transparent;
		opacity: 1;
	}

	@media (min-width: 768px) {
		.body {
			display: grid;
			grid-template-columns: minmax(0, 1fr) 0.75rem clamp(
					260px,
					var(--result-w),
					calc(100% - 360px - 0.75rem)
				);
			gap: 0;
			padding: 0;
			overflow: hidden;
			scrollbar-gutter: auto;
		}
		.controls {
			overflow-y: auto;
			scrollbar-gutter: stable;
			overscroll-behavior: contain;
			grid-column: 1;
			grid-row: 1;
			min-height: 0;
		}
		.body > :global(.divider) {
			display: block;
			grid-column: 2;
			grid-row: 1;
			cursor: col-resize;
			touch-action: none;
			margin: var(--space-2) 0;
			background: linear-gradient(var(--color-border), var(--color-border)) center / 2px 100%
				no-repeat;
		}
		.body > :global(.divider):hover,
		.body > :global(.divider):focus-visible {
			background-color: var(--color-accent-soft, transparent);
			background-image: linear-gradient(var(--color-accent), var(--color-accent));
			outline: none;
		}
		.result {
			order: 0;
			container-type: size;
			overflow: hidden;
			grid-column: 3;
			grid-row: 1;
			min-height: 0;
			margin: var(--space-2) var(--page-pad) var(--space-2) 0;
			padding: var(--space-3);
			border: 1px solid var(--color-border);
			border-radius: var(--radius-lg);
			box-shadow: var(--shadow-1);
		}
	}

	/* Desktop: padded column of section cards. */
	@media (min-width: 1024px) {
		.controls {
			gap: var(--space-3);
			padding: var(--space-3) var(--page-pad) 4rem;
		}
		.toolbar {
			margin-inline: calc(-1 * var(--page-pad));
		}
		.notice {
			padding: var(--space-3);
			border: 1px solid var(--color-border);
			border-radius: var(--radius);
		}
		.err-box {
			border-color: var(--color-danger);
		}
		.empty {
			padding: 0;
		}
		.result {
			margin-block: var(--space-3);
		}
		.body > :global(.divider) {
			margin-block: var(--space-3);
		}
	}
</style>
