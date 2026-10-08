import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the React dev server proxies /api to the local FastAPI process, so the
// frontend always talks to a same-origin /api path. In production Vercel routes /api to
// the Python function, meaning the browser code is identical in both environments and
// no CORS configuration is ever needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
