import { afterEach, describe, expect, it, vi } from "vitest";
import { chooseRecordingMimeType, formatDuration } from "./VoiceNoteScreen";

afterEach(() => vi.unstubAllGlobals());

describe("voice note recording helpers", () => {
  it("prefers WebM/Opus and falls back to MP4/AAC", () => {
    vi.stubGlobal("MediaRecorder", class {
      static isTypeSupported(type: string) {
        return type === "audio/mp4;codecs=mp4a.40.2";
      }
    });
    expect(chooseRecordingMimeType()).toBe("audio/mp4;codecs=mp4a.40.2");
  });

  it("lets the runtime select a format when none of the candidates are supported", () => {
    vi.stubGlobal("MediaRecorder", class { static isTypeSupported() { return false; } });
    expect(chooseRecordingMimeType()).toBe("");
  });

  it("formats the recorder timer without rounding up", () => {
    expect(formatDuration(61_999)).toBe("01:01");
  });
});
