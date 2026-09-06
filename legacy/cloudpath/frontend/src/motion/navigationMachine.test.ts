import { describe, expect, it } from "vitest";
import { createInitialNavigation, navigate, type NavigationSnapshot } from "./navigationMachine";

function at(screen: NavigationSnapshot["screen"], history: NavigationSnapshot["history"] = []): NavigationSnapshot {
  return { direction: "replace", history, nonce: 7, screen };
}

describe("navigationMachine", () => {
  it("keeps the initial home screen static", () => {
    expect(createInitialNavigation()).toEqual({
      direction: "replace",
      history: [],
      nonce: 0,
      screen: "home"
    });
  });

  it("pushes a different go target with a forward generation", () => {
    expect(navigate(at("home"), { type: "go", screen: "profile" })).toEqual({
      direction: "forward",
      history: ["home"],
      nonce: 8,
      screen: "profile"
    });
  });

  it("preserves same-screen go history without replaying the page", () => {
    expect(navigate(at("profile", ["home"]), { type: "go", screen: "profile" })).toEqual({
      direction: "replace",
      history: ["home", "profile"],
      nonce: 7,
      screen: "profile"
    });
  });

  it("pops non-empty history and only replays when the destination changes", () => {
    expect(navigate(at("profile", ["home", "community"]), { type: "back" })).toEqual({
      direction: "back",
      history: ["home"],
      nonce: 8,
      screen: "community"
    });
    expect(navigate(at("profile", ["home", "profile"]), { type: "back" })).toEqual({
      direction: "back",
      history: ["home"],
      nonce: 7,
      screen: "profile"
    });
  });

  it("returns an empty-history non-home screen to home and leaves home static", () => {
    expect(navigate(at("profile"), { type: "back" })).toEqual({
      direction: "back",
      history: [],
      nonce: 8,
      screen: "home"
    });
    expect(navigate(at("home"), { type: "back" })).toEqual(at("home"));
  });

  it("keeps source navigation forward and automatic replacements out of history", () => {
    expect(navigate(at("lesson", ["home"]), { type: "source" })).toEqual({
      direction: "forward",
      history: ["home", "lesson"],
      nonce: 8,
      screen: "source"
    });
    expect(navigate(at("source", ["home"]), { type: "source" })).toEqual({
      direction: "forward",
      history: ["home", "source"],
      nonce: 8,
      screen: "source"
    });
    expect(navigate(at("processing", ["home"]), { type: "replace", screen: "chapterConfirm" })).toEqual({
      direction: "replace",
      history: ["home"],
      nonce: 8,
      screen: "chapterConfirm"
    });
  });
});
