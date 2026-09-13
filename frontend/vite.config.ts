import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// Development only: the dev server proxies /api to the FastAPI process so the
// browser sees one origin, matching production where FastAPI serves build/.
export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/api': {
				target: 'http://127.0.0.1:8000',
				changeOrigin: false,
				ws: true
			}
		}
	}
});
