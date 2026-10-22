import { request } from "./transport";
export type AccountState = {
  account: {
    email: string;
    stage: string;
    interests: string[];
    goal: string;
    is_demo: boolean;
  } | null;
  user_id: string;
  legacy_profile: boolean;
  email_available: boolean;
  demo_available: boolean;
  demo_users: { email: string; nickname: string }[];
  learning_sessions: Record<string, string>;
};
export type CodeChallenge = {
  challenge_id: string;
  expires_in: number;
  retry_after: number;
  delivery: "email" | "demo";
  message: string;
  demo_code?: string;
};
export type Registration = {
  nickname: string;
  stage: string;
  interests: string[];
  goal: string;
};
export const accountsApi = {
  me: () => request<AccountState>("/api/auth/me", {}, 20000),
  code: (email: string, purpose: string) =>
    request<CodeChallenge>(
      "/api/auth/code",
      { method: "POST", body: JSON.stringify({ email, purpose }) },
      20000,
    ),
  verify: (
    challenge_id: string,
    code: string,
    registration?: Registration,
    legacy_sessions: string[] = [],
  ) =>
    request<AccountState>(
      "/api/auth/verify",
      {
        method: "POST",
        body: JSON.stringify({
          challenge_id,
          code,
          registration,
          legacy_sessions,
        }),
      },
      20000,
    ),
  logout: () => request("/api/auth/logout", { method: "POST" }, 15000),
  demoFriends: () =>
    request<{ count: number }>(
      "/api/auth/demo-friends",
      { method: "POST" },
      20000,
    ),
};
