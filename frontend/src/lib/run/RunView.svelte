<script lang="ts">
	// The run page for one workflow: header, user-designed sections, result
	// panel, and the generate bar. Phone: one scroll area (result above the
	// controls) and the bar pinned at the bottom; tablet/desktop: controls and
	// result as two self-scrolling columns.
	import { onDestroy, onMount, tick } from 'svelte';
	import { goto } from '$app/navigation';
	import Icon from '$lib/ui/Icon.svelte';
	import GenerateBar from './GenerateBar.svelte';
	import PresetsMenu from './PresetsMenu.svelte';
	import QueuePanel from './QueuePanel.svelte';
	import ResultPanel from './ResultPanel.svelte';
	import SectionCard from './SectionCard.svelte';
	import { RunState } from './run.svelte';
	import { statusInfo } from './status';
	import { runShortcut } from './shortcuts';
	import { DEFAULT_SEED_MAX, randomExactInt } from '$lib/controls/exact';

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
	let resultW = $state<number | null>(null);
	try {
		const saved = Number(localStorage.getItem(WIDTH_KEY));
		if (saved > 0) resultW = saved;
	} catch {
		/* storage blocked: default width */
	}

	function setWidth(px: number, save: boolean): void {
		const total = body?.clientWidth ?? 0;
		// ponytail: fixed 260px/360px minimums; make them tokens if designs need other bounds
		resultW = Math.round(Math.min(Math.max(px, 260), Math.max(260, total - 360)));
		if (save)
			try {
				localStorage.setItem(WIDTH_KEY, String(resultW));
			} catch {
				/* not persisted */
			}
	}
	function dragDivider(event: PointerEvent): void {
		const handle = event.currentTarget as HTMLElement;
		handle.setPointerCapture(event.pointerId);
		const right = body!.getBoundingClientRect().right;
		const move = (e: PointerEvent): void => setWidth(right - e.clientX, false);
		const up = (): void => {
			handle.removeEventListener('pointermove', move);
			handle.removeEventListener('pointerup', up);
			handle.removeEventListener('pointercancel', up);
			if (resultW) setWidth(resultW, true);
		};
		handle.addEventListener('pointermove', move);
		handle.addEventListener('pointerup', up);
		handle.addEventListener('pointercancel', up);
	}
	function keyDivider(event: KeyboardEvent): void {
		const step = event.shiftKey ? 64 : 16;
		const now = resultW ?? Math.round((body?.clientWidth ?? 0) * 0.4);
		if (event.key === 'ArrowLeft') setWidth(now + step, true);
		else if (event.key === 'ArrowRight') setWidth(now - step, true);
		else if (event.key === 'Home' || event.key === 'End')
			setWidth(event.key === 'Home' ? 9999 : 0, true);
		else return;
		event.preventDefault();
	}
	let presetsOpen = $state(false);
	let queueOpen = $state(false);
	const blocking = $derived(run.schema?.blocking ?? []);
	const warnings = $derived(run.schema?.warnings ?? []);
	const shortcutsId = $props.id();
	let shortcutsEnabled = $state(true);

	function onkeydown(event: KeyboardEvent): void {
		if (document.querySelector('dialog[open], :popover-open')) return;
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
			if (!seed) return;
			const bounds = seed.constraints;
			run.useSeed(
				seed,
				randomExactInt(
					bounds?.exact_min ?? (bounds?.min != null ? String(Math.trunc(bounds.min)) : '0'),
					bounds?.exact_max ??
						(bounds?.max != null ? String(Math.trunc(bounds.max)) : DEFAULT_SEED_MAX)
				)
			);
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
			<span class="sr-only">Workflow</span>
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
		<a class="btn" href={`/workflows/${workflowId}`} aria-label="Open designer" title="Design"
			><Icon name="design" size={16} /> <span class="lbl">Design</span></a
		>
		<button
			type="button"
			class="btn"
			aria-label="Presets"
			disabled={run.loading || run.schema === null}
			onclick={() => {
				presetsOpen = true;
				void run.presets.load();
			}}
		>
			<Icon name="bookmark" size={16} /> <span class="lbl">Presets</span>
		</button>
		<button type="button" class="btn" onclick={() => (queueOpen = true)}>Queue</button>
		<button type="button" class="btn" popovertarget={shortcutsId} aria-label="Keyboard shortcuts"
			>?</button
		>
		<div id={shortcutsId} popover class="shortcuts">
			<strong>Keyboard shortcuts</strong>
			<p><kbd>Ctrl/Cmd + Enter</kbd> Generate</p>
			<p><kbd>P</kbd> Focus first prompt</p>
			<p><kbd>R</kbd> Randomize first seed and use fixed policy</p>
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
		<div class="body" bind:this={body} style:--result-w={resultW ? `${resultW}px` : '40%'}>
			<div class="controls" style:--toolbar-h={`${toolbarH}px`}>
				{#if blocking.length > 0}
					<div class="notice err-box" role="alert">
						<strong>This workflow cannot be submitted yet</strong>
						{#each blocking as detail (detail.code + (detail.field ?? ''))}<p>
								{detail.message}
							</p>{/each}
					</div>
				{/if}
				{#if warnings.length > 0}
					<details class="notice">
						<summary>{warnings.length} mapping warning{warnings.length === 1 ? '' : 's'}</summary>
						{#each warnings as detail (detail.code + (detail.field ?? ''))}<p>
								{detail.message}
							</p>{/each}
					</details>
				{/if}

				<div class="toolbar" bind:clientHeight={toolbarH}>
					<div class="row filters">
						<button
							type="button"
							class="chip"
							aria-pressed={run.modifiedOnly}
							disabled={run.modifiedCount === 0 && !run.modifiedOnly}
							onclick={() => (run.modifiedOnly = !run.modifiedOnly)}
						>
							Show modified only{run.modifiedCount ? ` (${run.modifiedCount})` : ''}
						</button>
						{#if run.modifiedCount}
							<button type="button" class="chip" onclick={() => run.resetAll()}>Reset all</button>
						{/if}
					</div>
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
					{#if run.sections.length > 3}
						<nav class="jump" aria-label="Jump to section">
							{#each run.sections as section (section.id)}
								<button type="button" class="chip" onclick={() => jump(section.id)}
									>{section.title}</button
								>
							{/each}
						</nav>
					{/if}
				</div>

				{#each run.sections as section (section.id)}
					<SectionCard {run} {section} />
				{:else}
					<p class="muted">
						{#if run.modifiedOnly}
							No modified controls.
						{:else}
							This workflow has no visible controls. Place some in the <a
								href={`/workflows/${workflowId}`}>designer</a
							>.
						{/if}
					</p>
				{/each}
			</div>

			<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
			<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
			<div
				class="divider"
				role="separator"
				aria-orientation="vertical"
				aria-label="Resize result panel"
				tabindex="0"
				onpointerdown={dragDivider}
				onkeydown={keyDivider}
			></div>
			<aside class="result" aria-label="Result">
				<ResultPanel {run} />
			</aside>
		</div>
		<GenerateBar {run} />
		<PresetsMenu {run} bind:open={presetsOpen} />
		{#if queueOpen}<QueuePanel {run} onclose={() => (queueOpen = false)} />{/if}
	{/if}
</div>

<style>
	.run {
		min-width: 0;
	}
	.top {
		flex: none;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2) var(--page-pad);
		border-bottom: 1px solid var(--color-border);
		background: var(--color-surface-1);
	}
	.switcher {
		flex: 1 1 0;
		min-width: 6rem;
		max-width: 24rem;
	}
	.status {
		margin-left: auto;
	}
	.top .btn {
		flex: none;
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
	.link {
		padding: 0;
		margin-left: var(--space-1);
		font: inherit;
		color: var(--color-accent);
		background: none;
		border: 0;
		text-decoration: underline;
		cursor: pointer;
	}
	@media (max-width: 479px) {
		.top {
			flex-wrap: wrap;
		}
		.switcher {
			min-width: 6rem;
		}
		.lbl {
			display: none;
		}
	}
	.state {
		padding: var(--page-pad);
	}
	.err {
		color: var(--color-danger);
	}

	/* Phone: the body is the single scroll area, result card first. */
	.body {
		flex: 1 1 auto;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		display: flex;
		flex-direction: column;
		padding: 0 var(--page-pad) var(--space-3);
		gap: var(--space-3);
	}
	.divider {
		display: none;
	}
	.result {
		order: -1;
		flex: none;
		margin-top: var(--space-3);
		padding: var(--space-3);
		background: var(--color-surface-1);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
	}
	.controls {
		container: run / inline-size;
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		min-width: 0;
	}

	.toolbar {
		position: sticky;
		top: 0;
		z-index: 2;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin-inline: calc(-1 * var(--page-pad));
		padding: var(--space-2) var(--page-pad);
		background: var(--color-bg);
	}
	.draft {
		margin: 0;
		font-size: var(--text-xs);
		color: var(--color-text-faint);
	}
	.draft.warn {
		color: var(--color-danger);
		font-weight: 600;
	}
	.filters,
	.jump {
		overflow-x: auto;
		scrollbar-width: none;
	}
	.jump {
		display: flex;
		gap: var(--space-2);
		overflow-x: auto;
		scrollbar-width: none;
	}
	.filters .chip,
	.jump .chip {
		flex: none;
	}
	.notice {
		padding: var(--space-3);
		font-size: var(--text-sm);
		background: var(--color-surface-1);
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
	}
	.notice p {
		margin: var(--space-1) 0 0;
	}
	.err-box {
		color: var(--color-danger);
		border-color: var(--color-danger);
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
		}
		.controls {
			overflow-y: auto;
			overscroll-behavior: contain;
			padding: 0 var(--page-pad) var(--space-3);
			grid-column: 1;
			grid-row: 1;
			min-height: 0;
		}
		.divider {
			display: block;
			grid-column: 2;
			grid-row: 1;
			cursor: col-resize;
			touch-action: none;
			margin: var(--space-3) 0;
			background: linear-gradient(var(--color-border), var(--color-border)) center / 2px 100%
				no-repeat;
		}
		.divider:hover,
		.divider:focus-visible {
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
			margin: var(--space-3) var(--page-pad) var(--space-3) 0;
		}
	}
</style>
