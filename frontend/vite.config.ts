import { copyFileSync, cpSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const projectRoot = dirname(fileURLToPath(import.meta.url));
const certificateDirectory = resolve(projectRoot, "certs");
const certificateKeyPath = resolve(certificateDirectory, "lan-key.pem");
const certificatePath = resolve(certificateDirectory, "lan-cert.pem");
const hasLanCertificate = existsSync(certificateKeyPath) && existsSync(certificatePath);
const httpsOptions = hasLanCertificate
  ? {
      https: {
        key: readFileSync(certificateKeyPath),
        cert: readFileSync(certificatePath)
      }
    }
  : {};

function productionPublicAssetsPlugin() {
  return {
    name: "copy-production-public-assets",
    apply: "build" as const,
    closeBundle() {
      const publicRoot = resolve(projectRoot, "public");
      const outputRoot = resolve(projectRoot, "dist");
      mkdirSync(outputRoot, { recursive: true });
      for (const filename of ["manifest.webmanifest", "sw.js"]) {
        copyFileSync(resolve(publicRoot, filename), resolve(outputRoot, filename));
      }
      for (const relativeDirectory of ["icons", "assets/brand"]) {
        cpSync(
          resolve(publicRoot, relativeDirectory),
          resolve(outputRoot, relativeDirectory),
          { recursive: true }
        );
      }
    }
  };
}

if (!hasLanCertificate) {
  console.warn(
    "[vite] LAN HTTPS certificate not found. Run the project certificate setup before starting the PWA demo."
  );
}

export default defineConfig(({ command }) => ({
  publicDir: command === "serve" ? resolve(projectRoot, "public") : false,
  plugins: [react(), productionPublicAssetsPlugin()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_BOOKCOURSE_DEV_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true
      }
    },
    ...httpsOptions
  },
  preview: {
    host: true,
    port: 4173,
    ...httpsOptions
  },
  test: {
    exclude: [...configDefaults.exclude, "e2e/**"]
  }
}));
