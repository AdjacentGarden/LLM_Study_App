import { defineConfig, mergeConfig } from "vite";
import base from "./vite.config";
// Isolated hidden reliability tests. The normal deployment proxy is unchanged.
export default mergeConfig(base, defineConfig({
  server: { host: "127.0.0.1", port: 18127, strictPort: true,
    proxy: { "/api": "http://127.0.0.1:18126" } },
}));
