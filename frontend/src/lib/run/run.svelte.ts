// State of one open run page: schema + layout, the user's draft edits
// (persisted per profile and workflow), section disclosure, and submission.
import { tick } from 'svelte';
import { readJson, writeJson } from '$lib/ui/storage';
import { replaceState } from '$app/navigation';
import { api, apiJson, ApiRequestError, describeApiError } from '$lib/api';
import type {
	ControlDescriptor,
	ControlSchema,
	AspectRatioLayoutItem,
	EditValue,
	GenerationDetail,
	Page,
	WorkflowInfo,
	WorkflowLayout
} from '$lib/contracts';
import { WIDE, resolveLayout, type ResolvedControl } from '$lib/layout/model';
import { session } from '$lib/session.svelte';
import { settingsState } from '$lib/settings.svelte';
import { lastWorkflow } from '$lib/ui/lastWorkflow.svelte';
import { PresetsState } from './presets.svelte';
import { GenerationTracker } from './tracker.svelte';
import { DEFAULT_SEED_MAX, randomExactInt } from '$lib/controls/exact';
import { RANDOM_SEED, baseValue, coerceValue, sameValue, validateValue } from './values';

export const MORE_ID = '__more';
const PERSIST_MS = 300;

export interface ViewSection {
	id: string;
	title: string;
	columns: 1 | 2 | 3;
	defaultOpen: boolean;
	/** Boolean control rendered as the header switch; the body is shown only while it is on. */
	toggle?: ControlDescriptor;
	entries: ViewEntry[];
	/** Panel sections: the same entries split into rows of equal columns (stacked on phones). */
	rows?: { id: string; columns: ViewEntry[][] }[];
}

export interface ViewEntry {
	control: ControlDescriptor;
	height?: ControlDescriptor;
	ratio?: AspectRatioLayoutItem;
	span: 'auto' | 'full';
}

export class RunState {
	readonly workflowId: string;
	readonly tracker: GenerationTracker;
	readonly presets: PresetsState;

	workflows = $state<WorkflowInfo[]>([]);
	schema = $state<ControlSchema | null>(null);
	layout = $state<WorkflowLayout | null>(null);
	loading = $state(true);
	loadError = $state<string | null>(null);
	notFound = $state(false);

	/** Only controls whose value differs from the imported one. */
	draft = $state<Record<string, EditValue>>({});
	modifiedOnly = $state(false);
	open = $state<Record<string, boolean>>({});
	submitting = $state(false);
	submitError = $state<string | null>(null);
	cancelling = $state(false);
	cancelError = $state<string | null>(null);
	notice = $state<string | null>(null);
	/** Prompt styles applied at submit, in this order (ids of app/styles.py rows). */
	styleIds = $state<string[]>([]);
	mediaInput = $state<{ binding_id: string; id: string } | null>(null);
	/** False once a draft write failed: the draft will not survive a reload. */
	draftPersisted = $state(true);

	name = $derived(this.workflows.find((w) => w.id === this.workflowId)?.name ?? null);
	resolved = $derived(this.schema ? resolveLayout(this.schema, this.layout?.layout ?? null) : null);
	blockingReason = $derived(this.schema?.blocking[0]?.message ?? null);

	/** Edited, visible controls whose value cannot be submitted, in page order: binding id -> reason. */
	invalid = $derived.by((): Map<string, string> => {
		const out = new Map<string, string>();
		for (const section of this.sections) {
			for (const control of section.entries.flatMap((entry) =>
				entry.height ? [entry.control, entry.height] : [entry.control]
			)) {
				const id = control.binding_id;
				const message = id in this.draft ? validateValue(control, this.draft[id]) : null;
				if (message) out.set(id, message);
			}
		}
		return out;
	});
	invalidReason = $derived(
		this.invalid.size === 0
			? null
			: `${this.invalid.size} control${this.invalid.size === 1 ? ' has an invalid value' : 's have invalid values'}`
	);
	canGenerate = $derived(
		this.schema !== null &&
			this.blockingReason === null &&
			this.invalid.size === 0 &&
			!this.submitting
	);

	/** Resolved seed(s) of the latest generation for visible seed controls (from its effective_values). */
	seedResults = $derived.by((): { control: ControlDescriptor; value: string }[] => {
		const values = this.tracker.latest?.effective_values;
		if (!values || !this.schema) return [];
		const hidden = new Set(this.resolved?.hidden.map((control) => control.binding_id));
		return this.schema.controls.flatMap((control) => {
			const value = values[control.binding_id];
			const usable =
				control.component === 'seed' && !hidden.has(control.binding_id) && value != null;
			return usable ? [{ control, value: String(value) }] : [];
		});
	});

	/** Sections as rendered: hidden controls dropped, unplaced ones in a trailing "More". */
	sections = $derived.by((): ViewSection[] => {
		const resolved = this.resolved;
		if (!resolved) return [];
		const only = this.modifiedOnly;
		const keep = (control: ControlDescriptor): boolean => !only || control.binding_id in this.draft;
		const view = (controls: ResolvedControl[]): ViewEntry[] =>
			controls
				.filter((entry) => !entry.when || this.isOn(entry.when))
				.filter((entry) => keep(entry.control) || (entry.height && keep(entry.height)))
				.map((entry) => ({
					control: entry.control,
					height: entry.height,
					ratio: entry.item.kind === 'aspect_ratio' ? entry.item : undefined,
					span: entry.item.span ?? 'auto'
				}));
		const out: ViewSection[] = resolved.sections.map(({ section, controls, toggle, rows }) => ({
			id: section.id,
			title: section.title,
			columns: section.mode === 'panels' ? 1 : section.columns,
			defaultOpen: !section.collapsed,
			toggle,
			entries: view(controls),
			rows: rows
				?.map((row) => ({
					id: row.id,
					columns: row.columns.map((column) => view(column.controls))
				}))
				.filter((row) => row.columns.some((column) => column.length > 0))
		}));
		if (resolved.unplaced.length) {
			out.push({
				id: MORE_ID,
				title: 'More',
				columns: 2,
				defaultOpen: false,
				entries: resolved.unplaced
					.filter(keep)
					.map((control) => ({ control, span: WIDE.has(control.component) ? 'full' : 'auto' }))
			});
		}
		return out.filter(
			(section) =>
				section.entries.length > 0 ||
				(section.toggle && (!only || section.toggle.binding_id in this.draft))
		);
	});

	private persistTimer: ReturnType<typeof setTimeout> | undefined;

	constructor(workflowId: string) {
		this.workflowId = workflowId;
		this.tracker = new GenerationTracker(workflowId);
		this.presets = new PresetsState(this);
		this.open = readJson(`simpleui:sections:${workflowId}`, {});
	}

	private get draftKey(): string {
		return `simpleui:draft:${session.info?.profile?.id ?? 'default'}:${this.workflowId}`;
	}

	/** Delete only the saved corrections that no longer apply, then refetch the controls. */
	async removeStaleCorrections(selectors: readonly string[]): Promise<void> {
		try {
			for (const selector of new Set(selectors)) {
				await api(
					`/workflows/${this.workflowId}/corrections?scope=workflow&selector=${encodeURIComponent(selector)}`,
					{ method: 'DELETE' }
				);
			}
			this.schema = await api<ControlSchema>(`/workflows/${this.workflowId}/controls`);
		} catch (cause) {
			this.notice = describeApiError(cause);
		}
	}

	async load(): Promise<void> {
		if (!settingsState.data) await settingsState.load();
		this.loading = true;
		this.loadError = null;
		const [workflows, schema, layout] = await Promise.allSettled([
			api<Page<WorkflowInfo>>('/workflows?limit=200'),
			api<ControlSchema>(`/workflows/${this.workflowId}/controls`),
			api<WorkflowLayout>(`/workflows/${this.workflowId}/layout`)
		]);
		if (workflows.status === 'fulfilled') this.workflows = workflows.value.items;
		if (schema.status === 'rejected') {
			if (schema.reason instanceof ApiRequestError && schema.reason.status === 404) {
				this.notFound = true;
				lastWorkflow.clear();
			} else {
				this.loadError = describeApiError(schema.reason);
			}
			this.loading = false;
			return;
		}
		// Only `layout: null` in a successful response means "automatic"; a failed
		// request must not silently bring hidden controls back.
		if (layout.status === 'rejected') {
			this.loadError = describeApiError(layout.reason);
			this.loading = false;
			return;
		}
		this.layout = layout.value;
		this.schema = schema.value;
		lastWorkflow.set(this.workflowId);
		this.draft = this.restoreDraft(schema.value);
		this.loading = false;
		void this.tracker.start();
		const params = new URLSearchParams(location.search);
		const mediaId = params.get('send_media');
		const bindingId = params.get('binding');
		if (
			mediaId &&
			bindingId &&
			schema.value.controls.some((c) => c.binding_id === bindingId && c.component === 'file')
		)
			this.mediaInput = { id: mediaId, binding_id: bindingId };

		// Links from the gallery/history ("Reuse as draft") arrive as ?reuse=<generation>.
		const reuse = new URLSearchParams(location.search).get('reuse');
		if (reuse) {
			await this.reuse(reuse);
			replaceState(location.pathname, {});
		}
	}

	dispose(): void {
		this.flush();
		this.tracker.stop();
	}

	// --- drafts ---------------------------------------------------------------

	private restoreDraft(schema: ControlSchema): Record<string, EditValue> {
		const saved = readJson<Record<string, EditValue>>(this.draftKey, {});
		const byId = new Map(schema.controls.map((control) => [control.binding_id, control]));
		const draft: Record<string, EditValue> = {};
		for (const [id, value] of Object.entries(saved)) {
			const control = byId.get(id);
			if (control && !sameValue(control, value, baseValue(control))) draft[id] = value;
		}
		return draft;
	}

	valueFor(control: ControlDescriptor): EditValue {
		return control.binding_id in this.draft ? this.draft[control.binding_id] : baseValue(control);
	}

	/** A boolean control's current value (conditions and section switches). */
	isOn(control: ControlDescriptor): boolean {
		const value = this.valueFor(control);
		return value === true || value === 'true';
	}

	isModified(control: ControlDescriptor): boolean {
		return control.binding_id in this.draft;
	}

	setValue(control: ControlDescriptor, value: EditValue): void {
		if (this.mediaInput?.binding_id === control.binding_id) this.mediaInput = null;
		const next = { ...this.draft };
		if (sameValue(control, value, baseValue(control))) delete next[control.binding_id];
		else next[control.binding_id] = value;
		this.draft = next;
		this.schedulePersist();
	}

	reset(control: ControlDescriptor): void {
		if (!(control.binding_id in this.draft)) return;
		const next = { ...this.draft };
		delete next[control.binding_id];
		this.draft = next;
		this.schedulePersist();
	}

	resetAll(): void {
		this.draft = {};
		this.modifiedOnly = false;
		this.schedulePersist();
	}

	/** Replace the whole draft (preset apply); callers pass only values that differ from imported ones. */
	replaceDraft(draft: Record<string, EditValue>): void {
		this.draft = draft;
		this.schedulePersist();
	}

	/** The seed the latest generation resolved for this control, or null before the first run. */
	lastSeed(control: ControlDescriptor): string | null {
		return this.seedResults.find((r) => r.control.binding_id === control.binding_id)?.value ?? null;
	}

	get modifiedCount(): number {
		return Object.keys(this.draft).length;
	}

	private schedulePersist(): void {
		clearTimeout(this.persistTimer);
		this.persistTimer = setTimeout(() => this.flush(), PERSIST_MS);
	}

	private flush(): void {
		if (this.persistTimer === undefined) return;
		clearTimeout(this.persistTimer);
		this.persistTimer = undefined;
		this.draftPersisted = writeJson(this.draftKey, this.draft);
	}

	// --- sections -----------------------------------------------------------

	isOpen(section: ViewSection): boolean {
		// Filtering to modified controls would be pointless behind a closed section.
		return this.modifiedOnly || (this.open[section.id] ?? section.defaultOpen);
	}

	toggle(section: ViewSection): void {
		this.open = { ...this.open, [section.id]: !this.isOpen(section) };
		writeJson(`simpleui:sections:${this.workflowId}`, this.open);
	}

	setOpen(section: ViewSection, value: boolean): void {
		if (this.isOpen(section) === value) return;
		this.open = { ...this.open, [section.id]: value };
		writeJson(`simpleui:sections:${this.workflowId}`, this.open);
	}

	// --- reuse ----------------------------------------------------------------

	/** Load a past generation's effective values into the draft (file inputs excluded). */
	async reuse(generationId: string): Promise<void> {
		this.notice = null;
		this.mediaInput = null;
		const schema = this.schema;
		if (!schema) return;
		try {
			const generation = await api<GenerationDetail>(`/generations/${generationId}`);
			if (generation.workflow_id !== this.workflowId || !generation.effective_values) {
				this.notice = 'That generation has no settings to reuse for this workflow.';
				return;
			}
			const hidden = new Set(this.resolved?.hidden.map((control) => control.binding_id));
			const draft: Record<string, EditValue> = {};
			for (const control of schema.controls) {
				const id = control.binding_id;
				if (control.component === 'file' || hidden.has(id) || !(id in generation.effective_values))
					continue;
				const value = coerceValue(control, generation.effective_values[id]);
				if (!sameValue(control, value, baseValue(control))) draft[id] = value;
			}
			for (const savedInput of generation.inputs ?? []) {
				if (
					!schema.controls.some(
						(control) =>
							control.binding_id === savedInput.binding_id && control.component === 'file'
					)
				)
					continue;
				if (savedInput.source === 'media')
					this.mediaInput = { binding_id: savedInput.binding_id, id: savedInput.source_id };
				else draft[savedInput.binding_id] = savedInput.source_id;
			}
			this.draft = draft;
			this.schedulePersist();
			this.notice = 'Settings loaded from that generation.';
		} catch (cause) {
			this.notice = describeApiError(cause);
		}
	}

	// --- submit / cancel ------------------------------------------------------

	/** Open the section of the first invalid control, scroll to it and focus it. */
	async focusInvalid(): Promise<void> {
		const first = this.invalid.keys().next().value;
		if (first === undefined) return;
		const section = this.sections.find((s) =>
			s.entries.some((e) => e.control.binding_id === first || e.height?.binding_id === first)
		);
		if (section) this.setOpen(section, true);
		await tick();
		const el = document.getElementById(first);
		el?.scrollIntoView({ block: 'center' });
		el?.focus({ preventScroll: true });
	}

	async submit(): Promise<void> {
		const schema = this.schema;
		if (schema && this.invalid.size > 0 && !this.submitting) {
			void this.focusInvalid();
			return;
		}
		if (!schema || !this.canGenerate) return;
		if (settingsState.data?.profile.clear_generation_on_generate) this.tracker.clearResults();
		this.submitting = true;
		this.submitError = null;
		this.cancelError = null;
		this.notice = null;
		try {
			const hidden = new Set(this.resolved?.hidden.map((control) => control.binding_id));
			const edits: Record<string, EditValue> = Object.fromEntries(
				Object.entries(this.draft).filter(([id]) => !hidden.has(id))
			);
			const inputs =
				this.mediaInput && !hidden.has(this.mediaInput.binding_id)
					? { [this.mediaInput.binding_id]: { source: 'media', id: this.mediaInput.id } }
					: {};
			// A seed left at -1 becomes a fresh random one; anything else stays fixed.
			for (const control of schema.controls) {
				if (control.component !== 'seed' || hidden.has(control.binding_id)) continue;
				if (String(this.valueFor(control)) !== RANDOM_SEED) continue;
				const c = control.constraints;
				edits[control.binding_id] = randomExactInt(
					c?.exact_min ?? (c?.min != null ? String(Math.trunc(c.min)) : '0'),
					c?.exact_max ?? (c?.max != null ? String(Math.trunc(c.max)) : DEFAULT_SEED_MAX)
				);
			}
			// ponytail: a fresh key per click; GenerationService already guarantees a key
			// that did reach the server is never resubmitted upstream.
			const requestKey =
				typeof crypto.randomUUID === 'function'
					? crypto.randomUUID()
					: `${Date.now()}-${Math.random()}`;
			const detail = await apiJson<GenerationDetail>('/generations', 'POST', {
				workflow_id: this.workflowId,
				request_key: requestKey,
				edits,
				inputs,
				seed_policy: 'fixed',
				style_ids: this.styleIds
			});
			await this.tracker.adopt(detail);
		} catch (cause) {
			this.submitError = describeApiError(cause);
		} finally {
			this.submitting = false;
		}
	}

	async cancel(): Promise<void> {
		const id = this.tracker.latest?.id;
		if (!id || this.cancelling) return;
		this.cancelling = true;
		this.cancelError = null;
		try {
			await api(`/generations/${id}/cancel`, { method: 'POST' });
			await this.tracker.refresh();
		} catch (cause) {
			this.cancelError = describeApiError(cause);
		} finally {
			this.cancelling = false;
		}
	}
}
