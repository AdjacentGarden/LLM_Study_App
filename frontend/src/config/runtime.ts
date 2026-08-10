export const runtimeConfig = {
  apiBaseUrl: import.meta.env.VITE_BOOKCOURSE_API_BASE_URL ?? "http://127.0.0.1:8000",
  defaultUserId: import.meta.env.VITE_BOOKCOURSE_USER_ID ?? "local_user",
  useDemoRepository: import.meta.env.VITE_BOOKCOURSE_USE_DEMO_REPOSITORY === "true"
};
