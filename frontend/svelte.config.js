import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
export default {
	preprocess: vitePreprocess(),
	kit: {
		// Static SPA: FastAPI serves build/ and returns index.html for deep links.
		adapter: adapter({ pages: 'build', assets: 'build', fallback: 'index.html', precompress: false }),
		prerender: { entries: [] }
	}
};
