/**
 * Temporary, single-device DeepSeek configuration.
 *
 * VITE_DEEPSEEK_API_KEY is intentionally read from the gitignored .env.local
 * file. It remains mutable so tests can inject a mock value without ever
 * placing a credential in source control.
 */
export const deepSeekConfig = {
  mode: "auto" as "auto" | "demo",
  apiKey: String(import.meta.env.VITE_DEEPSEEK_API_KEY ?? "").trim(),
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  maxTokens: 900
};

export function hasDirectDeepSeekKey() {
  return deepSeekConfig.mode !== "demo" && deepSeekConfig.apiKey.trim().length > 0;
}

export function getAiRuntimeLabel() {
  return "学习助手";
}

export const deepSeekKeySetupMessage = "在线学习助手尚未启用：请在 gitignored 的 .env.local 中设置 VITE_DEEPSEEK_API_KEY 后重新构建。";
