import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: {
      '/dashboard/api': 'http://127.0.0.1:20128',
      '/dashboard/login': 'http://127.0.0.1:20128',
      '/dashboard/logout': 'http://127.0.0.1:20128'
    }
  }
});
