import assert from "node:assert/strict";
import { readFileSync, statSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const shell = readFileSync(new URL("../src/components/StudioShell.tsx", import.meta.url), "utf8");
const account = readFileSync(new URL("../src/components/AccountGate.tsx", import.meta.url), "utf8");

test("heavy secondary experiences stay outside the initial JavaScript path", () => {
  assert.match(shell, /lazy\(\(\) => loadStudio\(\)/);
  assert.match(app, /const SocialPage = lazy/);
  assert.match(account, /const RegisterPage = lazy/);
  assert.doesNotMatch(app, /from "\.\/components\/LearningStudio"/);
});

test("default avatar is small enough for immediate profile rendering", () => {
  const avatar = new URL("../public/assets/brand/profile-avatar-editorial-v3.webp", import.meta.url);
  assert.ok(statSync(avatar).size < 60_000, "default avatar must remain below 60 KB");
});
