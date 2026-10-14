import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The FastAPI app serves index.html at "/" and this build under "/static" (gatemoe/server/app.py).
// Everything is bundled (fonts, pdf.js worker): nodes run offline.
export default defineConfig({
  base: "/static/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: [
      { find: "@", replacement: path.resolve(import.meta.dirname, "./src") },
      // pdf.js "legacy" build: polyfilled for the older browsers common on school computers
      { find: /^pdfjs-dist$/, replacement: "pdfjs-dist/legacy/build/pdf.mjs" },
    ],
  },
  build: {
    outDir: path.resolve(import.meta.dirname, "../gatemoe/server/static"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1600,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000", "/zim": "http://127.0.0.1:8000" },
  },
});
