/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The browser only ever talks to the API through this proxy (or the same origin in
// production). No credential or provider URL is compiled into the bundle.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: { environment: 'jsdom', setupFiles: './src/test/setup.ts', globals: true, css: false, include: ['src/**/*.test.{ts,tsx}'] },
})
