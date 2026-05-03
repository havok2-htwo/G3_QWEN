import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        admin: resolve(__dirname, 'admin.html'),
        demo: resolve(__dirname, 'demo.html')
      }
    }
  },
  server: {
    host: '0.0.0.0',
    port: 5178,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8088',
        changeOrigin: true
      },
      '/v1': {
        target: 'http://127.0.0.1:8088',
        changeOrigin: true
      }
    }
  }
});
