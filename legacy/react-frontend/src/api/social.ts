import { request } from "./transport";
import type {
  SocialMe,
  SocialUser,
  SocialContacts,
  ChatSubmission,
  ChatMessage,
  ChatBatch,
} from "../types/social";
const post = <T>(path: string, body?: object) =>
  request<T>(
    path,
    { method: "POST", body: body ? JSON.stringify(body) : undefined },
    15000,
  );
export const socialApi = {
  me: () => request<SocialMe>("/api/social/me", {}, 15000),
  visibility: (enabled: boolean) =>
    post<SocialMe>("/api/social/discoverability", { enabled }),
  search: (query: string) =>
    request<SocialUser[]>(
      `/api/social/users?query=${encodeURIComponent(query)}`,
      {},
      15000,
    ),
  contacts: () => request<SocialContacts>("/api/social/contacts", {}, 15000),
  friend: (
    id: string,
    action:
      | "request"
      | "accept"
      | "decline"
      | "cancel"
      | "remove"
      | "block"
      | "unblock",
  ) => post<SocialUser>(`/api/social/friends/${id}/${action}`),
  chat: (id: string, after = 0, before = 0) =>
    request<ChatBatch>(
      `/api/social/chats/${id}?after=${after}&before=${before}`,
      {},
      15000,
    ),
  send: (id: string, body: ChatSubmission) =>
    post<ChatMessage>(`/api/social/chats/${id}`, body),
  read: (id: string, message_id: number) =>
    post<{ ok: boolean }>(`/api/social/chats/${id}/read`, { message_id }),
  acquire: (id: number) =>
    post<{
      status: "added" | "already_owned";
      book_id: string;
      resource_id: string | null;
    }>(`/api/social/messages/${id}/acquire`),
};
