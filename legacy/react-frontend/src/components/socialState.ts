import type { ChatMessage } from "../types/social";

export function messageNonce(): string {
  // getRandomValues also works on direct HTTP previews; randomUUID does not.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
export function mergeMessages(
  current: ChatMessage[],
  incoming: ChatMessage[],
): ChatMessage[] {
  const map = new Map(current.map((row) => [row.id, row]));
  incoming.forEach((row) => map.set(row.id, row));
  return [...map.values()].sort((a, b) => a.id - b.id);
}
