export type Field =
	| { key: string; label: string; help: string; type: 'text' | 'number' | 'checkbox' }
	| { key: string; label: string; help: string; type: 'select'; options: readonly string[] };

// Mirrors backend/app/settings/service.py HOST_SETTINGS. Keep both in sync.
export const HOST_FIELDS: Field[] = [
	{
		key: 'comfy_url',
		label: 'ComfyUI URL',
		help: 'Where the ComfyUI server on this computer listens.',
		type: 'text'
	},
	{
		key: 'comfy_output_dir',
		label: 'ComfyUI output folder',
		help: 'Generated media is read from here.',
		type: 'text'
	},
	{
		key: 'upload_max_bytes',
		label: 'Upload size limit (bytes)',
		help: 'Largest single upload accepted.',
		type: 'number'
	},
	{
		key: 'pending_cap',
		label: 'Pending generation cap',
		help: 'Most queued generations allowed at once.',
		type: 'number'
	},
	{
		key: 'catalog_refresh_min_seconds',
		label: 'Catalog refresh cooldown (seconds)',
		help: 'Minimum time between node catalog refreshes.',
		type: 'number'
	},
	{
		key: 'backup_dir',
		label: 'Backup folder',
		help: 'Where database backups are written.',
		type: 'text'
	}
];

// Mirrors backend/app/settings/service.py PROFILE_SETTINGS. Keep both in sync.
export const PROFILE_GROUPS: { title: string; fields: Field[] }[] = [
	{
		title: 'Appearance',
		fields: [
			{
				key: 'theme',
				label: 'Theme',
				help: 'Follow the system, or force light or dark.',
				type: 'select',
				options: ['system', 'light', 'dark']
			},
			{
				key: 'density',
				label: 'Density',
				help: 'Compact fits more controls on screen.',
				type: 'select',
				options: ['comfortable', 'compact']
			}
		]
	},
	{
		title: 'Generation',
		fields: [
			{
				key: 'show_advanced',
				label: 'Show advanced controls',
				help: 'Reveal rarely used workflow controls.',
				type: 'checkbox'
			},
			{
				key: 'live_previews',
				label: 'Live previews',
				help: 'Show intermediate images while generating.',
				type: 'checkbox'
			},
			{
				key: 'video_enabled',
				label: 'Video generation',
				help: 'Allow workflows that produce video.',
				type: 'checkbox'
			},
			{
				key: 'completion_sound',
				label: 'Completion sound',
				help: 'Play a sound when a generation finishes.',
				type: 'checkbox'
			},
			{
				key: 'clear_generation_on_startup',
				label: 'Clear generation results on startup',
				help: 'Start each app session with an empty generation result and gallery. Saved media stays in Gallery.',
				type: 'checkbox'
			},
			{
				key: 'clear_generation_on_generate',
				label: 'Clear generation results on Generate',
				help: 'Clear previous results when you generate. All outputs from the new batch remain available. Saved media stays in Gallery.',
				type: 'checkbox'
			}
		]
	},
	{
		title: 'Gallery',
		fields: [
			{
				key: 'thumbnail_size',
				label: 'Thumbnail size',
				help: 'Size of tiles in the gallery grid.',
				type: 'select',
				options: ['small', 'medium', 'large']
			},
			{
				key: 'gallery_autoplay',
				label: 'Autoplay videos in gallery',
				help: 'Start videos as soon as they open.',
				type: 'checkbox'
			},
			{
				key: 'gallery_page_size',
				label: 'Gallery page size',
				help: 'How many items to load at a time.',
				type: 'number'
			}
		]
	},
	{
		title: 'Privacy and history',
		fields: [
			{
				key: 'store_history',
				label: 'Store prompt/workflow history',
				help: 'Keep prompts and inputs so past generations can be reused. Turning this off does not delete media.',
				type: 'checkbox'
			}
		]
	}
];
