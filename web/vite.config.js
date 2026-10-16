import { resolve } from 'node:path'
import { defineConfig } from 'vite'

export default defineConfig({
  base: '/',
  build: {
    outDir: resolve(__dirname, '../scribeos/web_dist'),
    emptyOutDir: true,
    target: 'es2020',
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      input: {
        index: resolve(__dirname, 'index.html'),
        join: resolve(__dirname, 'join.html'),
      },
    },
  },
  server: {
    // `npm run dev` proxies API calls to a running `python -m scribeos --browser --port 5174`.
    proxy: {
      '/api': 'http://127.0.0.1:5174',
      '/auth': 'http://127.0.0.1:5174',
      '/ws': { target: 'ws://127.0.0.1:5174', ws: true },
    },
  },
})
