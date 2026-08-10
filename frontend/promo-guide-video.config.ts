import { defineConfig } from "playwright/test";

const port = Number(process.env.E2E_PORT ?? 4186);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "promo-guide-capture.spec.ts",
  outputDir: "./output/promo-guide-video/test-results",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  workers: 1,
  use: {
    browserName: "chromium",
    headless: true,
    launchOptions: {
      channel: "chromium",
      args: ["--disable-frame-rate-limit", "--disable-gpu-vsync"]
    },
    trace: "retain-on-failure"
  },
  webServer: {
    command: `npm run dev -- --port ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      VITE_BOOKCOURSE_API_BASE_URL: `http://127.0.0.1:${port}`,
      VITE_BOOKCOURSE_USER_ID: "promo_video_user",
      VITE_BOOKCOURSE_USE_DEMO_REPOSITORY: "true",
      VITE_DEMO_DELAY_MS: "90"
    }
  }
});
