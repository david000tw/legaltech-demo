import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 只綁本機：開發伺服器若對外開放，經它轉發的請求在後端看起來都來自 127.0.0.1。
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
