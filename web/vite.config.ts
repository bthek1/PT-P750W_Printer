/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  server: {
    // pt-server owns the USB device; the dev server forwards API calls to it.
    proxy: { '/api': 'http://127.0.0.1:8750' },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
    coverage: {
      include: ['src/**'],
      // Generated shadcn components and the React bootstrap are not our logic.
      exclude: ['src/components/ui/**', 'src/main.tsx', 'src/**/*.test.*', 'src/setupTests.ts'],
      thresholds: { statements: 90, branches: 90, functions: 90, lines: 90 },
    },
  },
})
