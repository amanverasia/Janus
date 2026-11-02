import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import { dashboardConfig } from './dashboard.config.ts';

export default defineConfig({
  plugins: [sveltekit(dashboardConfig)],
  server: {
    proxy: {
      '/dashboard/api': 'http://127.0.0.1:20128',
      '/dashboard/login': 'http://127.0.0.1:20128',
      '/dashboard/logout': 'http://127.0.0.1:20128'
    }
  }
});
