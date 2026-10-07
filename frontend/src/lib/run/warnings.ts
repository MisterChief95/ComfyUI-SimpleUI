// Sorts a schema's presentation-only warnings so the run page only raises a
// flag for things a person can act on.
import type { ErrorDetail } from '../contracts.ts';

/** Expected for hidden widgets, wildcard inputs and lazily expanded branches. */
const INFO_CODES = new Set(['flexible_input', 'undeclared_input', 'inactive_controls']);
const STALE_CODE = 'mapping_correction_stale';

export interface WarningGroups {
	/** Real problems; each is listed on its own. */
	actionable: ErrorDetail[];
	/** Saved corrections that no longer apply, collapsed into one summary. */
	stale: ErrorDetail[];
	/** Purely informational; never counted as a warning. */
	info: ErrorDetail[];
}

export function groupWarnings(warnings: readonly ErrorDetail[]): WarningGroups {
	const groups: WarningGroups = { actionable: [], stale: [], info: [] };
	for (const detail of warnings) {
		if (detail.code === STALE_CODE) groups.stale.push(detail);
		else if (INFO_CODES.has(detail.code)) groups.info.push(detail);
		else groups.actionable.push(detail);
	}
	return groups;
}
