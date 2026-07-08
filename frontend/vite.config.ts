import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Frontend talks to relative /api and /ws; Vite forwards to FastAPI,
      // so there is no CORS or hardcoded backend origin in app code.
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
