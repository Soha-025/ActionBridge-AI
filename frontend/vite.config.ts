import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy keeps the frontend same-origin (/api -> FastAPI on :8000), so the
// app works with no VITE_API_BASE_URL set. api.ts also honors that variable and
// falls back to http://localhost:8000 (backend CORS is "*"), so a direct call
// works too if you prefer to skip the proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
