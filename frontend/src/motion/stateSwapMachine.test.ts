import { describe, expect, it } from "vitest";
import {
  createStateSwapSnapshot,
  settleStateSwap,
  updateStateSwap
} from "./stateSwapMachine";

describe("stateSwapMachine", () => {
  it("starts idle and does not replay the initial value", () => {
    expect(createStateSwapSnapshot("等待中")).toEqual({
      state: "idle",
      visibleValue: "等待中",
      previousValue: "等待中",
      targetValue: "等待中"
    });
  });

  it("enters swapping only when the value changes", () => {
    const initial = createStateSwapSnapshot("10%");
    expect(updateStateSwap(initial, "10%", false)).toBe(initial);
    expect(updateStateSwap(initial, "11%", false)).toEqual({
      state: "swapping",
      visibleValue: "11%",
      previousValue: "10%",
      targetValue: "11%"
    });
  });

  it("lets the latest value replace an unfinished target without queueing", () => {
    const first = updateStateSwap(createStateSwapSnapshot("10%"), "11%", false);
    const latest = updateStateSwap(first, "12%", false);
    expect(latest).toEqual({
      state: "swapping",
      visibleValue: "12%",
      previousValue: "11%",
      targetValue: "12%"
    });
  });

  it("settles idempotently", () => {
    const swapping = updateStateSwap(createStateSwapSnapshot("等待中"), "完成", false);
    const settled = settleStateSwap(swapping);
    expect(settleStateSwap(settled)).toBe(settled);
    expect(settled).toEqual({
      state: "idle",
      visibleValue: "完成",
      previousValue: "完成",
      targetValue: "完成"
    });
  });

  it("jumps to the latest value when reduced motion is enabled", () => {
    expect(updateStateSwap(createStateSwapSnapshot("等待中"), "完成", true)).toEqual({
      state: "idle",
      visibleValue: "完成",
      previousValue: "完成",
      targetValue: "完成"
    });
  });
});
