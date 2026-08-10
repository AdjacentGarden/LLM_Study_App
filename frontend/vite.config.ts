import { existsSync, readFileSync } from "node:fs";
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

if (!hasLanCertificate) {
  console.warn(
    "[vite] LAN HTTPS certificate not found. Run the project certificate setup before starting the PWA demo."
  );
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
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
});
