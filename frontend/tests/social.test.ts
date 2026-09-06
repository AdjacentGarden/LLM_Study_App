import { test } from "node:test";
import assert from "node:assert/strict";
import { mergeMessages, messageNonce } from "../src/components/socialState.ts";
import type { ChatMessage } from "../src/types/social.ts";
const row = (id: number, text = "hello"): ChatMessage => ({
  id,
  text,
  mine: true,
  client_id: `id-${id}`,
  attachment: null,
  created: id,
});
test("history pages and retry echoes merge once in server cursor order", () => {
  const merged = mergeMessages(
    [row(7), row(9)],
    [row(2), row(7, "confirmed"), row(10)],
  );
  assert.deepEqual(
    merged.map((r) => r.id),
    [2, 7, 9, 10],
  );
  assert.equal(merged[1].text, "confirmed");
  assert.deepEqual(mergeMessages(merged, merged), merged);
});
test("nonce is available without secure-context randomUUID and has high entropy", () => {
  const values = Array.from({ length: 1000 }, () => messageNonce());
  assert.equal(new Set(values).size, 1000);
  assert.ok(values.every((v) => /^[a-f0-9]{32}$/.test(v)));
});
