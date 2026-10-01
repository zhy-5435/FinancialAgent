import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// 单一源前缀约定：前端只请求 /api/* ，由代理分发到各后端服务
// 后续新增服务（知识库管理、检索调试等）只需在此追加一条代理规则
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // L3 Agent 问答服务（src/agent/api.py）
      '/api/agent': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/agent/, ''),
      },
      // L2 检索服务（src/search/api.py），预留：直达检索/调试页未来复用该通道
      '/api/knowledge': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/knowledge/, ''),
      },
    },
  },
});
