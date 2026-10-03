<script lang="ts" module>
	// 24x24 stroke icons (Lucide-style geometry, drawn here; no dependency).
	// Add a name here and it becomes available everywhere via <Icon name="..." />.
	const PATHS = {
		generate: 'M6 4l14 8-14 8z',
		workflows: 'M4 5h6v5H4zM14 14h6v5h-6zM10 7.5h2a2 2 0 0 1 2 2v1M14 16.5h-2a2 2 0 0 1-2-2v-1',
		gallery: 'M4 5h16v14H4zM8.5 10a1.5 1.5 0 1 0 0-.01M4 17l5-5 4 4 3-3 4 4',
		history: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2',
		settings:
			'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h0a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
		design: 'M3 4h18v16H3zM3 9h18M9 9v11',
		grid: 'M3 4h18v16H3zM3 12h18M12 4v16',
		plus: 'M12 5v14M5 12h14',
		trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
		grip: 'M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01',
		'chevron-up': 'M6 15l6-6 6 6',
		'chevron-down': 'M6 9l6 6 6-6',
		'chevron-left': 'M15 6l-6 6 6 6',
		'chevron-right': 'M9 6l6 6-6 6',
		eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
		'eye-off':
			'M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.6 6.6A16 16 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 4.2-1M9.9 9.9a3 3 0 0 0 4.2 4.2',
		undo: 'M9 14L4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3',
		redo: 'M15 14l5-5-5-5M20 9H10a6 6 0 0 0 0 12h3',
		save: 'M5 3h11l4 4v14H5zM8 3v5h7V3M8 21v-7h8v7',
		search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-4.3-4.3',
		close: 'M6 6l12 12M18 6L6 18',
		more: 'M5 12h.01M12 12h.01M19 12h.01',
		check: 'M5 12.5l4.5 4.5L19 7.5',
		alert: 'M12 3l10 18H2zM12 10v5M12 18h.01',
		image: 'M4 4h16v16H4zM8.5 9.5a1 1 0 1 0 0-.01M4 17l5-5 4 4 3-3 4 4',
		video: 'M3 6h12v12H3zM15 10l6-3v10l-6-3',
		reset: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5',
		menu: 'M4 6h16M4 12h16M4 18h16',
		external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
		dice: 'M4 4h16v16H4zM8.5 8.5h.01M15.5 8.5h.01M12 12h.01M8.5 15.5h.01M15.5 15.5h.01',
		switch: 'M7 4L3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7',
		user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
		bookmark: 'M6 3h12v18l-6-4-6 4z',
		copy: 'M9 9h11v11H9zM5 15V4h11',
		edit: 'M4 20h4L19 9l-4-4L4 16zM13 7l4 4',
		upload: 'M12 16V4M7 9l5-5 5 5M4 16v4h16v-4',
		download: 'M12 4v12M7 11l5 5 5-5M4 16v4h16v-4',
		expand: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
		collapse: 'M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5',
		sliders: 'M4 6h9M17 6h3M15 4v4M4 12h3M11 12h9M9 10v4M4 18h11M19 18h1M17 16v4',
		select: 'M4 4h16v16H4zM8 12l3 3 5-6',
		compare: 'M4 4h16v16H4zM12 4v16',
		filter: 'M3 5h18l-7 8v6l-4 2v-8z'
	} as const;

	export type IconName = keyof typeof PATHS;
</script>

<script lang="ts">
	// <Icon name="plus" size={20} />. Decorative by default (aria-hidden); pass
	// `label` when the icon is the only content of its control.
	let { name, size = 20, label }: { name: IconName; size?: number; label?: string } = $props();
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="2"
	stroke-linecap="round"
	stroke-linejoin="round"
	role={label ? 'img' : undefined}
	aria-label={label}
	aria-hidden={label ? undefined : 'true'}
	focusable="false"
>
	<path d={PATHS[name]} />
</svg>

<style>
	svg {
		flex: none;
		display: inline-block;
		vertical-align: middle;
	}
</style>
