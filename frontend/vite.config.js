import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:7823',
    },
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
})
