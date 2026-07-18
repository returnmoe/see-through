import { defineConfig } from 'astro/config';
import svelte from '@astrojs/svelte';

export default defineConfig({
  integrations: [svelte()],
  output: 'static',
  build: {
    assets: '_assets',
    inlineStylesheets: 'never',
  },
  vite: {
    build: {
      sourcemap: false,
    },
  },
});
