/**
 * ESLint flat config.
 *
 * ## Why this file exists
 *
 * `npm run lint` was dead. ESLint 9 is installed, the script passed ESLint 8 flags
 * (`--ext`, `--report-unused-disable-directives`), and there was no
 * `eslint.config.(js|mjs|cjs)` at all -- so the command failed immediately. A quality
 * gate that cannot run is worse than no gate, because it reads as a passing check.
 *
 * Composed by hand rather than from the `typescript-eslint` meta package, which is not
 * installed; the parser and the plugin are, and that is all a flat config needs.
 */
import js from '@eslint/js'
import tsParser from '@typescript-eslint/parser'
import tsPlugin from '@typescript-eslint/eslint-plugin'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'

export default [
  {
    // Never lint build output or dependencies. `dist/` in particular contains the
    // bundled Cesium build, which is megabytes of generated code.
    ignores: ['dist/**', 'node_modules/**', 'coverage/**'],
  },

  js.configs.recommended,

  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        ecmaFeatures: { jsx: true },
      },
      globals: { ...globals.browser, ...globals.es2021 },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...tsPlugin.configs.recommended.rules,

      // The compiler already reports unresolved imports and unused locals, and
      // `tsc --noEmit` runs as part of the build. Turning the base rules off means lint
      // reports only what the type system cannot see.
      'no-undef': 'off',
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        // `_`-prefixed names are the convention for "deliberately unused".
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // Fire-and-forget promises in effects are a real bug class here: most data
      // loading starts that way, so a silent rejection loses data with no message.
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'prefer-const': 'error',
      'no-var': 'error',
    },
  },

  {
    // React Hooks. `recommended-latest` is the flat-config entry point in v5; the
    // older `recommended` export is the legacy shape and would be silently ignored,
    // leaving every hook rule unenforced.
    ...reactHooks.configs['recommended-latest'],
    files: ['**/*.{ts,tsx}'],
    plugins: {
      ...reactHooks.configs['recommended-latest'].plugins,
      'react-hooks': reactHooks,
    },
  },

  {
    // Fast Refresh only matters for files that export components. Applying it to
    // `view/` modules, which export hooks and constants, would flag every one of them.
    ...reactRefresh.configs.vite,
    files: ['src/components/**/*.tsx', 'src/App.tsx'],
    plugins: {
      ...reactRefresh.configs.vite.plugins,
      'react-refresh': reactRefresh,
    },
  },

  {
    // Node for the scripts themselves, *and* browser globals: the Playwright suites
    // pass callbacks that run inside the page, so `document` and `window` appear in
    // `scripts/*.mjs` even though the file itself is Node.
    files: ['scripts/**/*.mjs', 'vite.config.ts', 'eslint.config.mjs'],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
]
