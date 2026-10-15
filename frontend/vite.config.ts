import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The Python backend (make run) serves the API and the WebRTC signalling on :7860.
const backend = 'http://localhost:7860'

export default defineConfig(({ command }) => ({
  // Built files are served by bot.py under /builder/.
  base: command === 'build' ? '/builder/' : '/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': backend,
      '/start': backend,
      '/sessions': backend,
    },
  },
}))
