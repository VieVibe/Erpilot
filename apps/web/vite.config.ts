import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 开发期把 /api 代理到 FastAPI（uv run --package erpilot-api python -m erpilot_api）
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        onlyExplicitManualChunks: true,
        manualChunks(id) {
          if (!id.includes('/node_modules/')) return;
          if (/\/node_modules\/(react|react-dom|scheduler)\//.test(id)) return 'react-vendor';
          if (/\/node_modules\/(?:@rc-component\/|rc-)/.test(id)) return 'ui-primitives';
          if (/\/node_modules\/@ant-design\/icons(?:-svg)?\//.test(id)) return 'icons';
          if (/\/node_modules\/@ant-design\/cssinjs(?:-utils)?\//.test(id)) return 'styling';
          if (id.includes('/node_modules/@ant-design/x-markdown/')) return 'markdown';
          if (id.includes('/node_modules/@ant-design/x/')) return 'chat-ui';
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
