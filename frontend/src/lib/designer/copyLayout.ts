// Copy a layout from another workflow (pure; runs under `npm test`): keep only
// the items and hidden entries whose binding exists in this schema.
import type { LayoutDoc } from '../contracts.ts';
import { itemBindings, mapSectionItems, type SchemaLike } from '../layout/model.ts';

export interface CopyResult {
	doc: LayoutDoc;
	/** Controls (placed or hidden) carried over. */
	kept: number;
	/** Controls dropped because this workflow has no such binding. */
	dropped: number;
}

export function copyKnown(source: LayoutDoc, schema: SchemaLike): CopyResult {
	const known = new Set(schema.controls.map((control) => control.binding_id));
	let kept = 0;
	let dropped = 0;
	const keep = (id: string): boolean => {
		if (known.has(id)) kept += 1;
		else dropped += 1;
		return known.has(id);
	};
	const sections = source.sections.map((section) =>
		mapSectionItems(section, (items) =>
			items.filter((item) => {
				const ids = itemBindings(item);
				const compatible =
					ids.every((id) => known.has(id)) &&
					(item.kind === 'control' ||
						ids.every(
							(id) => schema.controls.find((c) => c.binding_id === id)?.logical_type === 'int'
						));
				if (compatible) kept += ids.length;
				else dropped += ids.length;
				return compatible;
			})
		)
	);
	return { doc: { ...source, sections, hidden: source.hidden.filter(keep) }, kept, dropped };
}

export function describeCopy(from: string, result: CopyResult): string {
	const { kept, dropped } = result;
	const controls = `${kept} control${kept === 1 ? '' : 's'}`;
	return (
		`Copied the layout of "${from}": ${controls}` +
		(dropped ? `; ${dropped} dropped (not in this workflow)` : '') +
		'. Unsaved: review it, then Save, or Undo to go back.'
	);
}
