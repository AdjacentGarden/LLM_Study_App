import { describe, expect, it } from "vitest";
import { studyToolDefinitions } from "./studyTools";

describe("study tool registry", () => {
  it("keeps the approved learning actions in a stable, extensible order", () => {
    expect(studyToolDefinitions.map((tool) => tool.id)).toEqual([
      "source",
      "assignment",
      "flashcards",
      "mistakes"
    ]);
  });

  it("provides learner-facing copy for every visible action", () => {
    expect(studyToolDefinitions.every((tool) => tool.title.trim() && tool.description.trim())).toBe(true);
    expect(new Set(studyToolDefinitions.map((tool) => tool.id)).size).toBe(studyToolDefinitions.length);
  });

  it("uses the original-reading action as the primary learning entry", () => {
    expect(studyToolDefinitions[0]).toMatchObject({ id: "source", title: "进入学习" });
  });
});
