import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../src/styles/library-profile.css", import.meta.url), "utf8");

test("every shelf cover rests in the same leftward direction without tinted tiles", () => {
  assert.match(css, /transform:rotate\(var\(--cover-tilt,-3deg\)\)/);
  assert.match(css, /\.shelf-volume\s*\{\s*--cover-tilt:-3deg;\s*\}/);
  assert.doesNotMatch(css, /\.shelf-volume:nth-child\(even\)/);
  assert.doesNotMatch(css, /shelf-tint/);
  assert.match(css, /\.shelf-cover\s*\{[^}]*background:transparent/);
});

test("community covers scale gently without alternating directions or cropping", () => {
  const community = readFileSync(new URL("../src/styles/community.css", import.meta.url), "utf8");
  assert.doesNotMatch(community, /community-post:nth-child/);
  assert.match(community, /object-fit:contain/);
  assert.match(community, /transform:rotate\(-3deg\) scale\(\.94\)/);
  assert.match(community, /\.community-post:hover>img\s*\{ transform:rotate\(-3deg\) scale\(1\.02\)/);
  assert.match(community, /prefers-reduced-motion:reduce/);
});

test("both columns share hover straightening and pressing takes precedence", () => {
  const hover = css.indexOf(".shelf-cover:hover > img");
  const press = css.indexOf(".shelf-cover:active > img");
  assert.ok(hover >= 0 && press > hover);
  assert.match(css, /\.shelf-cover:hover > img\s*\{\s*transform:rotate\(0deg\) translateY\(-5px\)/);
  assert.match(css, /\.shelf-cover:active > img\s*\{\s*transform:scale\(\.96\) rotate\(0deg\)/);
});
