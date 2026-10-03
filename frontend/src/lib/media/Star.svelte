<script lang="ts">
	// Favorite star; filled when on. Decorative: the parent button carries the label.
	let { filled = false, size = 18 }: { filled?: boolean; size?: number } = $props();
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	fill={filled ? 'currentColor' : 'none'}
	stroke="currentColor"
	stroke-width="2"
	stroke-linejoin="round"
	aria-hidden="true"
	focusable="false"
>
	<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" />
</svg>
