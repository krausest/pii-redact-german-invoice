import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Dev: Vite serves the SPA (with HMR) and proxies API calls straight to the
// backend service on :8000. Prod: `vite build` -> dist/, served by backend
// as static files (PII_STATIC_DIR).
//
// Target is 127.0.0.1, NOT localhost: uvicorn binds IPv4 loopback by default, but
// Node resolves "localhost" to ::1 (IPv6) first on macOS, so a "localhost" target
// proxies to a dead IPv6 address and fails with ECONNREFUSED.
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
