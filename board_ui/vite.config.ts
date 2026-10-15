import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: "/app/",          // 云端挂在 /app 子路径，静态资源基路径必须同步。
  server: {
    // 本地开发代理：实验室页与看板共用 8800 本地看板服务的 /api。
    proxy: { "/api": "http://127.0.0.1:8800" },
  },
})
