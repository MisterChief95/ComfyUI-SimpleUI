// Pure preset helpers (no Svelte, runs under `npm test`).
//
// A preset stores only the controls the user had modified. Applying one
// replaces the draft with those values (everything else returns to the
// imported value), keeping only controls that still exist, are visible and
// are not file inputs.
import type { ControlDescriptor, EditValue } from '../contracts.ts';
import { baseValue, coerceValue, sameValue } from './values.ts';

export interface ApplyPlan {
	draft: Record<string, EditValue>;
	/** Preset values that reached the draft (or already equal the imported value). */
	applied: number;
	/** Keys not in this workflow's current schema. */
	unknown: number;
	/** Keys for hidden or file controls (never edited from the run page). */
	unusable: number;
}

export function planApply(
	controls: ControlDescriptor[],
	hidden: ReadonlySet<string>,
	values: Record<string, unknown>
): ApplyPlan {
	const byId = new Map(controls.map((control) => [control.binding_id, control]));
	const plan: ApplyPlan = { draft: {}, applied: 0, unknown: 0, unusable: 0 };
	for (const [id, raw] of Object.entries(values)) {
		const control = byId.get(id);
		if (!control) plan.unknown += 1;
		else if (control.component === 'file' || hidden.has(id)) plan.unusable += 1;
		else {
			// int values may arrive as JSON numbers: coerce to the ExactInt string.
			const value = coerceValue(control, raw);
			plan.applied += 1;
			if (!sameValue(control, value, baseValue(control))) plan.draft[id] = value;
		}
	}
	return plan;
}

/** What "Save current" stores: the modified draft entries of controls that exist, finite numbers only. */
export function presetValues(
	controls: ControlDescriptor[],
	draft: Record<string, EditValue>
): Record<string, EditValue> {
	const ids = new Set(controls.filter((c) => c.component !== 'file').map((c) => c.binding_id));
	return Object.fromEntries(
		Object.entries(draft).filter(
			([id, value]) => ids.has(id) && (typeof value !== 'number' || Number.isFinite(value))
		)
	);
}

/** "Applied 5 values. 2 skipped (1 not in this workflow, 1 hidden or file input)." */
export function describeApply(name: string, plan: ApplyPlan): string {
	const skipped = plan.unknown + plan.unusable;
	const noun = (n: number): string => `${n} value${n === 1 ? '' : 's'}`;
	let text = `Preset "${name}" applied: ${noun(plan.applied)}.`;
	if (skipped > 0) {
		const parts: string[] = [];
		if (plan.unknown) parts.push(`${plan.unknown} not in this workflow`);
		if (plan.unusable) parts.push(`${plan.unusable} for hidden or file controls`);
		text += ` ${skipped} skipped (${parts.join(', ')}).`;
	}
	return text;
}
