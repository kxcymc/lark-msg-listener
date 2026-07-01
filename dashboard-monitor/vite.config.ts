import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 本地开发服务配置：仅启动前端 Vite dev server。
// 后端本地服务（Fastify）独立运行，前端通过 /api 代理访问。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 4318,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:4317',
        changeOrigin: true,
      },
    },
  },
});
