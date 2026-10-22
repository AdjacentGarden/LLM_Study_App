import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../src/styles/formal-product.css", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const profile = readFileSync(new URL("../src/components/ProfileDashboard.tsx", import.meta.url), "utf8");

function luminance(hex: string) {
  const rgb = hex.match(/[a-f\d]{2}/gi)!.map(value => Number.parseInt(value, 16) / 255);
  const linear = rgb.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
}

function contrast(foreground: string, background: string) {
  const [bright, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (bright + .05) / (dark + .05);
}

test("formal body and muted text meet normal-text contrast on white", () => {
  for (const token of ["formal-body", "formal-muted", "formal-blue"]) {
    const color = css.match(new RegExp(`--${token}:\\s*(#[a-f\\d]{6})`, "i"))?.[1];
    assert.ok(color, `${token} must be defined as a six-digit color`);
    assert.ok(contrast(color, "#ffffff") >= 4.5, `${token} must reach 4.5:1 contrast`);
  }
});

test("release UI keeps touch targets and keyboard focus visible", () => {
  assert.match(css, /min-height:\s*44px/);
  assert.match(css, /outline:\s*3px solid var\(--formal-blue\)/);
  assert.match(app, /aria-current=\{active \? "page" : undefined\}/);
});

test("reader type control supports 100 to 200 percent and a large-type layout", () => {
  assert.match(profile, /type="range" min="1" max="2" step="\.1"/);
  assert.match(app, /Math\.min\(2, Math\.max\(1,/);
  assert.match(app, /is-large-type/);
  assert.match(css, /\.stage\.is-large-type/);
});
