import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const gatewayUrl = process.env.CUSP_GATEWAY_URL ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: gatewayUrl, ws: true },
      '/health': gatewayUrl,
    },
  },
})
