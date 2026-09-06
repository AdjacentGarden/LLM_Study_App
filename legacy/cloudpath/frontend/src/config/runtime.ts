export const runtimeConfig = {
  // Same-origin is the safe production default. Local development can still
  // point at a standalone backend through VITE_BOOKCOURSE_API_BASE_URL.
  apiBaseUrl: (import.meta.env.VITE_BOOKCOURSE_API_BASE_URL ?? "").replace(/\/$/, ""),
  defaultUserId: import.meta.env.VITE_BOOKCOURSE_USER_ID ?? "local_user",
  useDemoRepository: import.meta.env.VITE_BOOKCOURSE_USE_DEMO_REPOSITORY === "true"
};

export function setVerifiedUserId(userId: string) {
  const normalized = userId.trim();
  if (!normalized) throw new Error("Verified user id is empty");
  runtimeConfig.defaultUserId = normalized;
}

export function userStorageNamespace() {
  return encodeURIComponent(runtimeConfig.defaultUserId || "unverified");
}
