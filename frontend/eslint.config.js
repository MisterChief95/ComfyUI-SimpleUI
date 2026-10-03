import js from '@eslint/js';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import ts from 'typescript-eslint';

export default ts.config(
	{ ignores: ['build/', '.svelte-kit/', 'node_modules/'] },
	js.configs.recommended,
	...ts.configs.recommended,
	...svelte.configs.recommended,
	{ languageOptions: { globals: { ...globals.browser, ...globals.node } } },
	{
		files: ['**/*.svelte', '**/*.svelte.ts'],
		languageOptions: { parserOptions: { parser: ts.parser } }
	},
	{
		// Static SPA served at the site root (no `paths.base`), and plain Map/Set are used for
		// non-reactive lookups; both rules flag those intentionally.
		rules: {
			// `const { a: _a, ...rest } = obj` is the idiom for omitting a key.
			'@typescript-eslint/no-unused-vars': [
				'error',
				{ varsIgnorePattern: '^_', ignoreRestSiblings: true }
			],
			'svelte/no-navigation-without-resolve': 'off',
			'svelte/prefer-svelte-reactivity': 'off'
		}
	}
);
