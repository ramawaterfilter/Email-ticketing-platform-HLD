import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true, // the Google redirect address is tied to this port
    // Same origin as the API in development, exactly as in production behind the reverse proxy.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
