import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../src/styles/library-profile.css", import.meta.url), "utf8");

test("alternating covers set only the resting angle, not a higher-specificity transform", () => {
  assert.match(css, /transform:rotate\(var\(--cover-tilt,-3deg\)\)/);
  assert.match(css, /\.shelf-volume:nth-child\(even\)\s*\{\s*--cover-tilt:3deg;\s*\}/);
  assert.doesNotMatch(css, /\.shelf-volume:nth-child\(even\)[^{]*img\s*\{[^}]*transform:/);
});

test("both columns share hover straightening and pressing takes precedence", () => {
  const hover = css.indexOf(".shelf-cover:hover > img");
  const press = css.indexOf(".shelf-cover:active > img");
  assert.ok(hover >= 0 && press > hover);
  assert.match(css, /\.shelf-cover:hover > img\s*\{\s*transform:rotate\(0deg\) translateY\(-5px\)/);
  assert.match(css, /\.shelf-cover:active > img\s*\{\s*transform:scale\(\.96\) rotate\(0deg\)/);
});
