import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.VITE_API_BASE_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/health": apiTarget,
      "/markets": apiTarget,
      "/analytics": apiTarget,
      "/ops": apiTarget,
      "/auth": apiTarget,
      "/trading": apiTarget,
    },
  },
});
