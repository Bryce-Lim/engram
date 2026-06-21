import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Build straight into web/static so the Python server can serve it.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: path.resolve(__dirname, '../static'),
    emptyOutDir: true,
  },
  server: {
    // For `npm run dev`, proxy the API to the Python server.
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
