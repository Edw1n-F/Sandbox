import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import cesium from 'vite-plugin-cesium'

export default defineConfig({
  plugins: [react(), cesium()],
  resolve: {
    // Mirrors `paths` in tsconfig.json. Without this the `@/...` imports typecheck
    // but fail to resolve at runtime, because Vite does not read tsconfig paths.
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // Overlay PNGs are served by the backend at /static (StaticFiles mount in
      // app/main.py). Without this the relative overlay URL hits the Vite dev
      // server, which answers with index.html, the image fails to decode, and
      // the layer silently never appears. In production the API and the static
      // files are same-origin, so no proxy is involved.
      '/static': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // /health reports the GenAI configuration, and the panel's provider badge
      // reads it. It lives at the root rather than under /api, so without this
      // the badge would silently never resolve and the UI would fall back to
      // saying nothing -- which is the same as saying "fine".
      '/health': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
  // Pre-bundle every MUI entry point up front. A new import found mid-session makes
  // Vite re-optimise and swap the whole deps folder, and that swap is a bulk delete
  // which this environment's safe-delete guard kills the dev server over.
  optimizeDeps: {
    include: [
      '@mui/material/Alert',
      '@mui/material/AlertTitle',
      '@mui/material/Box',
      '@mui/material/Button',
      '@mui/material/Chip',
      '@mui/material/CircularProgress',
      '@mui/material/Collapse',
      '@mui/material/CssBaseline',
      '@mui/material/Fab',
      '@mui/material/Fade',
      '@mui/material/IconButton',
      '@mui/material/Slider',
      '@mui/material/Stack',
      '@mui/material/TextField',
      '@mui/material/Typography',
      '@mui/material/styles',
    ],
  },
  build: {
    target: 'esnext',
  },
})
