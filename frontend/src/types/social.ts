import type { SharedCard } from "./api";
export type SocialUser = {
  user_id: string;
  nickname: string;
  avatar_url: string | null;
  relationship:
    | "self"
    | "none"
    | "friend"
    | "incoming"
    | "outgoing"
    | "unavailable";
};
export type SocialMe = SocialUser & { discoverable: boolean };
export type SocialContacts = {
  friends: SocialUser[];
  incoming: SocialUser[];
  outgoing: SocialUser[];
  blocked: SocialUser[];
  conversations: (SocialUser & {
    preview: string;
    last_at: number;
    unread: number;
  })[];
};
export type AttachmentKind =
  | "book"
  | "flashcards"
  | "note"
  | "chapter"
  | "points";
export type AttachmentSpec = {
  kind: AttachmentKind;
  book_id: string;
  rights_confirmed: true;
  resource_id?: string;
  session_id?: string;
  course_id?: string;
  card_ids?: string[];
};
export type ChatAttachment = {
  kind: AttachmentKind;
  book_id: string;
  book_title: string;
  cover_url: string | null;
  title: string;
  content: { body?: string; cards?: SharedCard[] };
};
export type ChatMessage = {
  id: number;
  mine: boolean;
  client_id: string | null;
  text: string;
  attachment: ChatAttachment | null;
  created: number;
};
export type ChatBatch = {
  peer: SocialUser;
  can_chat: boolean;
  messages: ChatMessage[];
  has_more: boolean;
};
export type ChatSubmission = {
  client_id: string;
  text: string;
  attachment?: AttachmentSpec;
};
