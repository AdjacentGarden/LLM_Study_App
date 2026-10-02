/** Direct browser credentials are disabled; production AI is configured on the backend. */
export const deepSeekConfig = {
  mode: "demo" as "auto" | "demo",
  apiKey: "",
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  maxTokens: 900
};
export function hasDirectDeepSeekKey() {
  return deepSeekConfig.mode !== "demo" && deepSeekConfig.apiKey.trim().length > 0;
}
export function getAiRuntimeLabel() { return "学习助手"; }
export const deepSeekKeySetupMessage = "在线学习助手尚未启用，请配置后端学习服务。";
