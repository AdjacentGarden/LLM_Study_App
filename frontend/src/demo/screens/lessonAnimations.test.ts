import { describe, expect, it } from "vitest";
import { lessonAnimationForAsset } from "./lessonAnimations";

describe("lesson animation catalog", () => {
  it("links the meiosis introduction figure to its explainer", () => {
    expect(lessonAnimationForAsset("asset_ai_meiosis_dna_replication_v1")).toEqual(expect.objectContaining({
      triggerLabel: "看懂减数分裂",
      durationLabel: "7 秒",
      videoUrl: "/assets/lesson/meiosis-explainer-demo-v1.mp4"
    }));
  });

  it("keeps ordinary lesson figures unchanged", () => {
    expect(lessonAnimationForAsset("asset_ai_unrelated")).toBeNull();
    expect(lessonAnimationForAsset(null)).toBeNull();
  });
});
