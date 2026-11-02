import adapter from '@sveltejs/adapter-static';
import type { Config } from '@sveltejs/kit/vite';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { readFileSync } from 'node:fs';

const packageVersion = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf8')
).version;

export const dashboardConfig = {
  adapter: adapter({ fallback: 'index.html' }),
  preprocess: vitePreprocess(),
  paths: { base: '/dashboard/ui' },
  version: { name: packageVersion, pollInterval: 0 }
} satisfies Config;
