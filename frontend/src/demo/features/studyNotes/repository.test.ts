import { describe, expect, it } from "vitest";
import { createSilentWavBlob, createStudyNoteId } from "./repository";

describe("study note repository helpers", () => {
  it("creates type-prefixed unique note ids", () => {
    const first = createStudyNoteId("voice");
    const second = createStudyNoteId("voice");
    expect(first).toMatch(/^voice-note-/u);
    expect(second).not.toBe(first);
  });

  it("requires real recordings instead of inventing silent sample audio",()=>{
    expect(()=>createSilentWavBlob()).toThrow("请录制真实音频");
  });
});
