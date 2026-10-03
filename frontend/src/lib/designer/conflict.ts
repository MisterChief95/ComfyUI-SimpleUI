import type { LayoutDoc, LayoutSection } from '../contracts.ts';
import { itemBindings, locate, sectionItems } from '../layout/model.ts';

/** Changes saved elsewhere since this designer loaded, including order and placement. */
export function layoutChanges(before: LayoutDoc, after: LayoutDoc): string[] {
	const changes: string[] = [];
	const previous = new Map(before.sections.map((s, index) => [s.id, { s, index }]));
	const shape = (s: LayoutSection): string => s.mode === 'panels' ? `panels (${s.rows.map((r) => r.columns.length).join('+') || 'no rows'})` : `${s.columns} columns`;
	for (const [index, section] of after.sections.entries()) {
		const old = previous.get(section.id);
		if (!old) changes.push(`Section added: ${section.title}`);
		else if (JSON.stringify([old.index, old.s.title, shape(old.s), old.s.collapsed]) !== JSON.stringify([index, section.title, shape(section), section.collapsed])) {
			changes.push(`Section ${old.s.title}: position ${old.index + 1}, ${shape(old.s)}, ${old.s.collapsed ? 'collapsed' : 'open'} → ${section.title}, position ${index + 1}, ${shape(section)}, ${section.collapsed ? 'collapsed' : 'open'}`);
		}
	}
	for (const section of before.sections) {
		if (!after.sections.some((s) => s.id === section.id)) changes.push(`Section removed: ${section.title}`);
	}
	function positions(doc: LayoutDoc): Map<string, string> {
		const out = new Map(doc.hidden.map((id) => [id, 'hidden']));
		for (const section of doc.sections) {
			if (section.toggle) out.set(section.toggle, `${section.title} [${section.id}], header switch`);
			for (const item of sectionItems(section)) {
				for (const id of itemBindings(item)) {
					const where = locate(doc, id);
					const cell = where && where !== 'hidden' && where.row ? `row ${where.row}, column ${where.column}, ` : '';
					out.set(id, `${section.title} [${section.id}], ${cell}item ${(where && where !== 'hidden' ? where.index : 0) + 1}, ${JSON.stringify(item)}`);
				}
			}
		}
		return out;
	}
	const left = positions(before), right = positions(after);
	for (const id of new Set([...left.keys(), ...right.keys()])) {
		if (left.get(id) !== right.get(id)) changes.push(`Control ${id}: ${left.get(id) ?? 'unplaced'} → ${right.get(id) ?? 'unplaced'}`);
	}
	return changes;
}
