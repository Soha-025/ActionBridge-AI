import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy keeps the frontend same-origin (/api -> FastAPI on :8000). It is
// opt-in: api.ts defaults to a direct http://127.0.0.1:8000 call, so set
// VITE_API_BASE_URL="" in frontend/.env to route through this proxy instead.
// Backend CORS is "*", so both paths work.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // Explicit IPv4. "localhost" resolves to ::1 on Node 17+, while uvicorn
        // binds 127.0.0.1 by default — the proxy then never connected and every
        // /api request hung until the client timeout instead of returning data.
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
