import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', '../static/dist', 'node_modules'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // Unused values are usually a leftover from a refactor; allow the
      // conventional `_`-prefixed placeholder for deliberate ones.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],

      'no-console': ['warn', { allow: ['warn', 'error'] }],
    },
  },

  // ─────────────────────────────────────────────
  //  Inherited debt
  //
  //  ESLint 10 ships the compiler-backed react-hooks rules, which flag
  //  patterns throughout the pre-existing chat UI (refs written during
  //  render, setState in effect bodies). Those files work today and
  //  reworking them is a behaviour-changing refactor, not part of
  //  hardening auth. They are listed per file and per rule so the debt is
  //  visible and shrinks deliberately — new files get the full ruleset.
  // ─────────────────────────────────────────────
  {
    files: [
      'src/app/providers/ChatProvider.tsx',
      'src/app/providers/SocketProvider.tsx',
      'src/components/chat/SourceChip.tsx',
    ],
    rules: { 'react-hooks/refs': 'off' },
  },
  {
    files: [
      'src/components/chat/AgentThinking.tsx',
      'src/components/chat/ChatComposer.tsx',
      'src/components/panel/DocsPane.tsx',
    ],
    rules: { 'react-hooks/set-state-in-effect': 'off' },
  },
  {
    files: ['src/components/chat/AgentThinking.tsx', 'src/components/chat/MessageList.tsx'],
    rules: { 'react-hooks/purity': 'off' },
  },
  {
    // Both deliberately match ANSI escapes and NUL placeholders while
    // sanitising agent output for display.
    files: ['src/components/chat/ValidationCard.tsx', 'src/lib/markdown.tsx'],
    rules: { 'no-control-regex': 'off' },
  },
  {
    // Co-locating a context's provider with its `use*` hook is the
    // convention here. It costs a full reload on edit instead of a hot
    // update, which is an acceptable trade for keeping them together.
    files: ['src/app/providers/*.tsx', 'src/components/ui/Button.tsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
);
