import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies /api to the Python API (uv run python -m nirnaya.web).
// `npm run build` writes dist/, which the API serves at http://localhost:8000.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
  build: { outDir: "dist", emptyOutDir: true },
});
