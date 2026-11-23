import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import fs from "node:fs";
import path from "node:path";

// MediaPipe's WebAssembly files for live pose matching on the Capture page are served by this
// app at /mediapipe/ (copied from node_modules), so the browser never fetches them from a CDN.
const WASM_DIR = path.resolve(__dirname, "node_modules/@mediapipe/tasks-vision/wasm");
function mediapipeWasm(): Plugin {
  return {
    name: "mediapipe-wasm",
    configureServer(server) {
      server.middlewares.use("/mediapipe", (req, res, next) => {
        const file = path.join(WASM_DIR, path.basename(req.url ?? ""));
        if (!fs.existsSync(file)) return next();
        res.setHeader("Content-Type", file.endsWith(".wasm") ? "application/wasm" : "text/javascript");
        fs.createReadStream(file).pipe(res);
      });
    },
    writeBundle(opts) {
      const out = path.join(opts.dir ?? "dist", "mediapipe");
      fs.mkdirSync(out, { recursive: true });
      for (const f of fs.readdirSync(WASM_DIR)) fs.copyFileSync(path.join(WASM_DIR, f), path.join(out, f));
    },
  };
}

// Dev: Vite on 5173 proxies /api to the FastAPI server on 8000.
// Prod: `vite build` writes frontend/dist, which FastAPI serves itself.
export default defineConfig({
  plugins: [react(), tailwindcss(), mediapipeWasm()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
