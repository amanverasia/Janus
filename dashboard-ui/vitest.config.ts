import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

// Separate from vite.config.ts so the `resolve.conditions` + `test` block do
// not leak into the production build (which would change the committed
// dashboard bundle). Svelte 5 ships separate server/client builds; under
// vitest's node resolution the server build is selected (where `mount` is
// unavailable), so component tests force the browser condition.
export default defineConfig({
  plugins: [sveltekit()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    setupFiles: ['./vitest-setup.ts']
  },
  resolve: {
    conditions: ['browser']
  }
});
