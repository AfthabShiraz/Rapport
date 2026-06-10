import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const backend = 'http://localhost:8001'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/agents': backend,
      '/calls': backend,
      '/optimizations': backend,
      '/analytics': backend,
      '/health': backend,
      '/ws': { target: 'ws://localhost:8001', ws: true },
    },
  },
})
