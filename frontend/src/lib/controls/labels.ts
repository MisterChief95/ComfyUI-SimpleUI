// Display-only friendly control labels. The backend labels a control
// "<node title or class and id>: <Pretty input name>" (mapping/controls.py
// _qualify/_pretty), which reads like a node graph. Rewrite only labels still in
// that generated shape, and only when the result is unique in the schema; a
// saved relabel or anything uncertain is left exactly as the server sent it.
import type { ControlDescriptor, ControlSchema } from '../contracts.ts';

const FRIENDLY: Record<string, string> = {
	cfg: 'CFG scale',
	ckpt_name: 'Checkpoint',
	vae_name: 'VAE',
	lora_name: 'LoRA',
	sampler_name: 'Sampler',
	scheduler: 'Scheduler',
	denoise: 'Denoise strength',
	steps: 'Steps',
	seed: 'Seed',
	noise_seed: 'Seed',
	batch_size: 'Batch size',
	width: 'Width',
	height: 'Height',
	filename_prefix: 'File name prefix',
	strength_model: 'Model strength',
	strength_clip: 'CLIP strength'
};

function pretty(name: string): string {
	const text = name.replaceAll('_', ' ').trim();
	return text ? text.charAt(0).toUpperCase() + text.slice(1).toLowerCase() : name;
}

/** The friendly label for one control, or null when its label is not the generated shape. */
function candidate(control: ControlDescriptor): string | null {
	const generated = `: ${pretty(control.input_name)}`;
	if (!control.label.endsWith(generated) || control.label.length === generated.length) return null;
	return FRIENDLY[control.input_name] ?? pretty(control.input_name);
}

export function friendlySchema(schema: ControlSchema): ControlSchema {
	const proposed = new Map<string, string>();
	const counts = new Map<string, number>();
	for (const control of schema.controls) {
		const label = candidate(control);
		if (label) proposed.set(control.binding_id, label);
		// Collisions are counted against every label the user would then see.
		const shown = label ?? control.label;
		counts.set(shown, (counts.get(shown) ?? 0) + 1);
	}
	if (proposed.size === 0) return schema;
	return {
		...schema,
		controls: schema.controls.map((control) => {
			const label = proposed.get(control.binding_id);
			return label && counts.get(label) === 1 ? { ...control, label } : control;
		})
	};
}
