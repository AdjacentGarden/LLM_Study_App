import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import postcss from "postcss";
import ts from "typescript";

const readSource = (path: string) => readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");
const entry = readSource("demo/DemoDirectEntry.tsx");
const tokens = postcss.parse(readSource("demo/styles/tokens.css"));
const css = postcss.parse(readSource("demo/styles/card-system.css"));
const base = postcss.parse(readSource("demo/styles/base.css"));
const typography = postcss.parse(readSource("demo/styles/typography.css"));
const navigation = readSource("demo/components/ui.tsx");
const tools = readSource("demo/AdditionalTools.tsx");
const profile = readSource("components/ProfileDashboard.tsx");

function token(name: string) {
  let value: string | undefined;
  tokens.walkDecls(`--${name}`, declaration => { value = declaration.value; });
  assert.ok(value, `${name} must be defined in the active demo tokens`);
  return value;
}

function declaration(root: postcss.Root, selector: string, property: string) {
  let value: string | undefined;
  root.walkRules(rule => {
    if (!rule.selectors.includes(selector)) return;
    rule.walkDecls(property, item => { value = item.value; });
  });
  assert.ok(value, `${selector} must declare ${property}`);
  return value;
}

function luminance(hex: string) {
  assert.match(hex, /^#[a-f\d]{6}$/i);
  const rgb = hex.match(/[a-f\d]{2}/gi)!.map(value => Number.parseInt(value, 16) / 255);
  const linear = rgb.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
}

function contrast(foreground: string, background: string) {
  const [bright, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (bright + .05) / (dark + .05);
}

// Exercise the small pure helpers used by the live JSX without mounting the
// API-connected page or duplicating its scale calculation in the test.
function toolHelper(name: string) {
  const source = ts.createSourceFile("AdditionalTools.tsx", tools, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const helper = source.statements.find(statement => ts.isFunctionDeclaration(statement) && statement.name?.text === name);
  assert.ok(helper, `${name} must remain available to the live font control`);
  const javascript = ts.transpileModule(helper.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return runInNewContext(`${javascript}\n${name}`) as (value: number) => any;
}

test("quality rules are loaded by the active demo entry", () => {
  for (const path of ["tokens", "base", "card-system", "typography"]) {
    assert.ok(entry.includes(`import "./styles/${path}.css"`), `${path} must be in the live stylesheet path`);
  }
  assert.ok(entry.indexOf('import "./styles/card-system.css"') > entry.indexOf('import "./styles/base.css"'));
});

test("demo body, secondary, muted and accent text meet normal-text contrast on reading surfaces", () => {
  for (const name of ["color-text", "color-text-secondary", "color-text-muted", "color-text-placeholder", "color-primary"]) {
    for (const surface of ["color-surface", "color-surface-soft", "color-surface-tint", "color-bg"]) {
      assert.ok(contrast(token(name), token(surface)) >= 4.5, `${name} on ${surface} must reach 4.5:1 contrast`);
    }
  }
});

test("release UI keeps touch targets, keyboard focus and current navigation visible", () => {
  assert.ok(Number.parseFloat(token("touch-target-min")) >= 44);
  for (const property of ["min-inline-size", "min-block-size"]) {
    assert.equal(declaration(css, "button", property), "var(--touch-target-min)");
  }
  assert.equal(declaration(css, "textarea", "min-block-size"), "var(--touch-target-min)");
  assert.equal(declaration(css, ":where(button, a, input, select, textarea):focus-visible", "outline"), "3px solid var(--color-focus)");
  assert.ok(contrast(token("color-focus"), token("color-surface")) >= 3);
  assert.match(navigation, /aria-current=\{item\.active \? "page" : undefined\}/);
  assert.match(navigation, /aria-label="主导航"/);
});

test("profile font control supports and persists 100 to 200 percent with bounded stored values", () => {
  assert.match(profile, /aria-label="字体大小" type="range" min="1" max="2" step="\.1"/);
  assert.match(tools, /useState\(\(\) => clampFontScale\(Number\(storage\.safeGet\("zhiwo\.font-scale"\)\)/);
  assert.match(tools, /onFont=\{value => \{ const scale = clampFontScale\(value\); storage\.safeSet\("zhiwo\.font-scale", String\(scale\)\); setFontScale\(scale\); \}\}/);
  const clamp = toolHelper("clampFontScale");
  for (const [value, expected] of [[0, 1], [1, 1], [1.5, 1.5], [2, 2], [3, 2], [NaN, 1], [Infinity, 1]]) {
    assert.equal(clamp(value), expected);
  }
});

test("font control scales active demo typography while retaining a flowing and scrollable tool layout", () => {
  assert.match(tools, /className="screen-stack additional-tools"[^>]*style=\{toolTypography\(fontScale\)\}/);
  const styles = toolHelper("toolTypography");
  for (const scale of [1, 1.5, 2]) {
    for (const name of ["type-title", "type-subtitle", "type-body", "type-caption"]) {
      assert.equal(styles(scale)[`--${name}`], `${Number.parseFloat(token(name)) * scale}px`);
      assert.ok(typography.toString().includes(`font-size: var(--${name}) !important`), `${name} must be consumed by active typography`);
    }
  }
  assert.equal(declaration(typography, ".app-shell :where(*)", "font-size"), "var(--type-body)");
  assert.equal(declaration(typography, ".app-shell :where(*)", "line-height"), "1.4");
  assert.equal(declaration(base, ".screen-stack", "flex-direction"), "column");
  assert.equal(declaration(base, ".screen-content", "overflow-y"), "auto");
});
