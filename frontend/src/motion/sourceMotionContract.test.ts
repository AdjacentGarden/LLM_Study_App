/// <reference types="node" />

import fs from "node:fs";
import path from "node:path";
import postcss from "postcss";
import { describe, expect, it } from "vitest";
import {
  auditCssMotion,
  auditGsapMotion,
  semanticMotionTokenFixture,
  type MotionSource
} from "./sourceMotionContract";

const workspaceRoot = process.cwd();

function filesBelow(directory: string, matcher: (fileName: string) => boolean): MotionSource[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return filesBelow(entryPath, matcher);
    return matcher(entry.name) ? [{ filePath: entryPath, sourceText: fs.readFileSync(entryPath, "utf8") }] : [];
  });
}

const cssSources = filesBelow(path.join(workspaceRoot, "src", "styles"), (fileName) => fileName.endsWith(".css"));
const productionTypeScriptSources = filesBelow(
  path.join(workspaceRoot, "src"),
  (fileName) => /\.tsx?$/.test(fileName)
    && !fileName.endsWith(".test.ts")
    && fileName !== "sourceMotionContract.ts"
);

function cssFixture(sourceText: string): MotionSource[] {
  return [{ filePath: "fixture.css", sourceText: `${semanticMotionTokenFixture}\n${sourceText}` }];
}

const approvedToVars = `{
  x: targetPosition.x,
  y: targetPosition.y,
  width: targetPosition.width,
  height: targetPosition.height,
  duration: localSlowMotionDurationSeconds,
  ease: localStateGsapEase,
  overwrite: "auto"
}`;

function gsapFixture({
  toVars = approvedToVars,
  extra = "",
  importName = "gsap",
  importSource = "gsap",
  filePath = "src/components/ui.tsx"
}: {
  toVars?: string;
  extra?: string;
  importName?: string;
  importSource?: string;
  filePath?: string;
} = {}): MotionSource[] {
  return [{
    filePath,
    sourceText: `
      import ${importName} from "${importSource}";
      const localSlowMotionDurationSeconds = 0.2;
      const localStateGsapEase = "power2.inOut";
      const selection = {};
      const targetPosition = { x: 10, y: 20, width: 30, height: 40 };
      gsap.registerPlugin(useGSAP);
      gsap.killTweensOf(selection);
      gsap.set(selection, {
        width: targetPosition.width,
        height: targetPosition.height
      });
      gsap.to(selection, ${toVars});
      gsap.set(selection, {
        x: targetPosition.x,
        y: targetPosition.y,
        width: targetPosition.width,
        height: targetPosition.height
      });
      ${extra}
    `
  }];
}

describe("source motion contract", () => {
  it("parses and audits every production stylesheet with no timing bypass", () => {
    expect(cssSources.length).toBeGreaterThan(0);
    for (const source of cssSources) {
      expect(() => postcss.parse(source.sourceText, { from: source.filePath })).not.toThrow();
    }
    expect(auditCssMotion(cssSources)).toEqual([]);
  });

  it("accepts only the production GSAP file and exact call inventory", () => {
    expect(productionTypeScriptSources.some((source) => source.sourceText.includes('from "gsap"'))).toBe(true);
    expect(auditGsapMotion(productionTypeScriptSources)).toEqual([]);
  });

  it.each([
    {
      name: "unknown custom property",
      css: `.x { transition: var(--missing-motion); }`,
      expected: "references unknown custom property"
    },
    {
      name: "cyclic custom property",
      css: `:root { --a: var(--b); --b: var(--a); } .x { transition: var(--a); }`,
      expected: "custom property cycle"
    },
    {
      name: "fallback hidden in var",
      css: `.x { transition: opacity var(--motion-duration-local-fast, 9s) var(--motion-ease-local-state); }`,
      expected: "must not contain a fallback"
    },
    {
      name: "complete shorthand hidden in a custom property",
      css: `:root { --bundle: opacity var(--motion-duration-local-fast) var(--motion-ease-local-state); } .x { transition: var(--bundle); }`,
      expected: "may hide a motion shorthand"
    },
    {
      name: "second comma animation with uppercase literal timing",
      css: `.x { animation: ok var(--motion-duration-local-base) var(--motion-ease-local-state), rogue 2S EASE infinite; }`,
      expected: "duration must use a semantic token"
    },
    {
      name: "transition-property all in a later longhand list item",
      css: `.x { transition-property: opacity, ALL; transition-duration: var(--motion-duration-local-fast); transition-timing-function: var(--motion-ease-local-state); }`,
      expected: "transition-property must not be all"
    },
    {
      name: "unapproved longhand infinite animation",
      css: `.x { animation-name: safe, rogue; animation-duration: var(--motion-duration-local-base), var(--motion-duration-loading); animation-timing-function: var(--motion-ease-local-state), linear; animation-iteration-count: 1, infinite; }`,
      expected: "longhand infinite animation is not approved"
    },
    {
      name: "timing drift in a media query",
      css: `@media (max-width: 600px) { .x { animation-duration: var(--motion-duration-local-slow); } }`,
      expected: "conditional rule introduces a timing override"
    },
    {
      name: "semantic token swap hidden in a responsive shorthand",
      css: `@media (orientation: landscape) { .x { transition: opacity var(--motion-duration-local-slow) var(--motion-ease-local-state); } }`,
      expected: "conditional rule introduces a timing override"
    },
    {
      name: "zero duration outside an approved snap",
      css: `.x { transition: opacity 0s var(--motion-ease-local-state); }`,
      expected: "duration must use a semantic token"
    },
    {
      name: "loading duration used by finite motion",
      css: `.x { animation: finite var(--motion-duration-loading) var(--motion-ease-local-state); }`,
      expected: "uses the loading duration on finite motion"
    },
    {
      name: "linear finite easing in a longhand",
      css: `.x { animation-name: finite; animation-duration: var(--motion-duration-local-base); animation-timing-function: linear; }`,
      expected: "easing must use a semantic token"
    }
  ])("rejects CSS mutation: $name", ({ css, expected }) => {
    expect(auditCssMotion(cssFixture(css)).join("\n")).toContain(expected);
  });

  it.each([
    {
      name: "snap allowlist with an unrelated selector branch",
      css: `.ai-orb.dragging, .rogue { transition-property: transform; transition-duration: 0s; transition-timing-function: var(--motion-ease-local-state); }`,
      expected: "duration must use a semantic token"
    },
    {
      name: "stagger allowlist with an unrelated selector branch",
      css: `[data-motion-course-card-state="entering"], .rogue { animation-delay: calc(var(--motion-course-card-index) * 30ms); }`,
      expected: "has an unapproved delay"
    },
    {
      name: "will-change allowlist with an unrelated selector branch",
      css: `.motion-screen-transition[data-motion-state="transitioning"] > .motion-screen-surface, .rogue { will-change: opacity, transform; }`,
      expected: "enables permanent will-change"
    },
    {
      name: "reduced-motion OR query",
      css: `@media (prefers-reduced-motion: reduce), (max-width: 600px) { .x { transition-duration: 1ms; } }`,
      expected: "conditional rule introduces a timing override"
    },
    {
      name: "negated reduced-motion query",
      css: `@media not (prefers-reduced-motion: reduce) { .x { transition-duration: 1ms; } }`,
      expected: "conditional rule introduces a timing override"
    },
    {
      name: "only-screen reduced-motion query",
      css: `@media only screen and (prefers-reduced-motion: reduce) { .x { transition-duration: 1ms; } }`,
      expected: "conditional rule introduces a timing override"
    },
    {
      name: "nested conditional reduced-motion query",
      css: `@supports (display: grid) { @media (prefers-reduced-motion: reduce) { .x { transition-duration: 1ms; } } }`,
      expected: "conditional rule introduces a timing override"
    },
    {
      name: "container timing override",
      css: `@container card (min-width: 20rem) { .x { animation-duration: var(--motion-duration-local-fast); } }`,
      expected: "conditional rule introduces a timing override"
    },
    {
      name: "supports timing override",
      css: `@supports (display: grid) { .x { transition-timing-function: var(--motion-ease-local-state); } }`,
      expected: "conditional rule introduces a timing override"
    }
  ])("rejects selector or conditional allowlist bypass: $name", ({ css, expected }) => {
    expect(auditCssMotion(cssFixture(css)).join("\n")).toContain(expected);
  });

  it("accepts only the explicit loading loops, including Sprite Strip and a valid longhand list", () => {
    const failures = auditCssMotion(cssFixture(`
      .spinner { animation: motion-spinner var(--motion-duration-loading) linear infinite; }
      .sprite { animation: processing-cloud-sprite-loading var(--motion-duration-loading) var(--motion-ease-sprite) infinite; }
      .skeleton {
        animation-name: motion-skeleton-pulse;
        animation-duration: var(--motion-duration-loading);
        animation-timing-function: var(--motion-ease-local-state);
        animation-iteration-count: infinite;
      }
      .shimmer { animation: study-loading-shimmer var(--motion-duration-loading) linear infinite; }
    `));
    expect(failures).toEqual([]);
  });

  it("accepts the exact production-shaped GSAP calls", () => {
    expect(auditGsapMotion(gsapFixture())).toEqual([]);
  });

  it.each([
    {
      name: "spread before semantic timing",
      fixture: { toVars: `{ ...targetPosition, duration: localSlowMotionDurationSeconds, ease: localStateGsapEase, overwrite: "auto" }` },
      expected: "GSAP vars must not contain spread"
    },
    {
      name: "spread after semantic timing",
      fixture: { toVars: `{ x: targetPosition.x, y: targetPosition.y, width: targetPosition.width, height: targetPosition.height, duration: localSlowMotionDurationSeconds, ease: localStateGsapEase, overwrite: "auto", ...targetPosition }` },
      expected: "GSAP vars must not contain spread"
    },
    {
      name: "non-inline variables object",
      fixture: { toVars: "vars", extra: `const vars = ${approvedToVars};` },
      expected: "GSAP vars must be an inline object literal"
    },
    {
      name: "duplicate duration property",
      fixture: { toVars: `{ x: targetPosition.x, y: targetPosition.y, width: targetPosition.width, height: targetPosition.height, duration: localSlowMotionDurationSeconds, duration: 9, ease: localStateGsapEase, overwrite: "auto" }` },
      expected: "GSAP vars keys must exactly be"
    },
    {
      name: "computed timing property",
      fixture: { toVars: `{ x: targetPosition.x, y: targetPosition.y, width: targetPosition.width, height: targetPosition.height, ["duration"]: localSlowMotionDurationSeconds, ease: localStateGsapEase, overwrite: "auto" }` },
      expected: "GSAP vars require direct identifier properties"
    },
    {
      name: "wrong property order",
      fixture: { toVars: `{ y: targetPosition.y, x: targetPosition.x, width: targetPosition.width, height: targetPosition.height, duration: localSlowMotionDurationSeconds, ease: localStateGsapEase, overwrite: "auto" }` },
      expected: "GSAP vars keys must exactly be"
    },
    {
      name: "wrong overwrite value",
      fixture: { toVars: `{ x: targetPosition.x, y: targetPosition.y, width: targetPosition.width, height: targetPosition.height, duration: localSlowMotionDurationSeconds, ease: localStateGsapEase, overwrite: false }` },
      expected: "GSAP overwrite must be \"auto\""
    },
    ...["delay", "repeat", "repeatDelay", "yoyo", "stagger", "keyframes", "defaults", "onRepeat", "unknown"].map((key) => ({
      name: `unapproved ${key} control`,
      fixture: { toVars: `{ x: targetPosition.x, y: targetPosition.y, width: targetPosition.width, height: targetPosition.height, duration: localSlowMotionDurationSeconds, ease: localStateGsapEase, overwrite: "auto", ${key}: 1 }` },
      expected: "GSAP vars keys must exactly be"
    })),
    {
      name: "GSAP object alias",
      fixture: { extra: `const engine = gsap;` },
      expected: "GSAP object and methods must not escape direct calls"
    },
    {
      name: "GSAP method assignment",
      fixture: { extra: `const tween = gsap.to;` },
      expected: "GSAP object and methods must not escape direct calls"
    },
    {
      name: "GSAP parameter default",
      fixture: { extra: `function tween(engine = gsap) { return engine; }` },
      expected: "GSAP object and methods must not escape direct calls"
    },
    {
      name: "GSAP object wrapper",
      fixture: { extra: `const registry = { gsap };` },
      expected: "GSAP object and methods must not escape direct calls"
    },
    {
      name: "GSAP array wrapper",
      fixture: { extra: `const registry = [gsap];` },
      expected: "GSAP object and methods must not escape direct calls"
    },
    {
      name: "GSAP returned from a function",
      fixture: { extra: `function expose() { return gsap; }` },
      expected: "GSAP object and methods must not escape direct calls"
    },
    {
      name: "GSAP passed to a wrapper",
      fixture: { extra: `wrap(gsap);` },
      expected: "GSAP object and methods must not escape direct calls"
    },
    {
      name: "dynamic GSAP method selection",
      fixture: { extra: `gsap["to"](selection, ${approvedToVars});` },
      expected: "GSAP object and methods must not escape direct calls"
    },
    {
      name: "indirect module lookup",
      fixture: { extra: `load("gsap");` },
      expected: "indirect GSAP lookup is not allowed"
    },
    {
      name: "static GSAP subpath import",
      fixture: { importSource: "gsap/dist/gsap" },
      expected: `GSAP package entry "gsap/dist/gsap" is not allowed`
    },
    {
      name: "case-normalized static GSAP subpath import",
      fixture: { importSource: "GSAP/Dist/gsap" },
      expected: `GSAP package entry "GSAP/Dist/gsap" is not allowed`
    },
    {
      name: "dynamic GSAP subpath string import",
      fixture: { extra: `void import("gsap/ScrollTrigger");` },
      expected: "indirect GSAP lookup is not allowed: gsap/ScrollTrigger"
    },
    {
      name: "dynamic GSAP subpath template import",
      fixture: { extra: "void import(`gsap/dist/gsap`);" },
      expected: "indirect GSAP lookup is not allowed: gsap/dist/gsap"
    },
    {
      name: "CommonJS GSAP root require",
      fixture: { extra: `require("gsap");` },
      expected: "indirect GSAP lookup is not allowed: gsap"
    },
    {
      name: "CommonJS GSAP subpath template require",
      fixture: { extra: "require(`gsap/dist/gsap`);" },
      expected: "indirect GSAP lookup is not allowed: gsap/dist/gsap"
    },
    {
      name: "TypeScript import-equals GSAP subpath require",
      fixture: { extra: `import gsapCore = require("gsap/gsap-core");` },
      expected: "indirect GSAP lookup is not allowed: gsap/gsap-core"
    },
    {
      name: "GSAP package re-export",
      fixture: { extra: `export * from "gsap/all";` },
      expected: "indirect GSAP lookup is not allowed: gsap/all"
    },
    ...["from", "fromTo", "quickTo"].map((method) => ({
      name: `alternate ${method} API`,
      fixture: { extra: `gsap.${method}(selection, ${approvedToVars});` },
      expected: `GSAP API ${method} is not on the production allowlist`
    })),
    {
      name: "timeline chain with a position parameter",
      fixture: { extra: `gsap.timeline().to(selection, ${approvedToVars}, "<");` },
      expected: "GSAP API timeline is not on the production allowlist"
    },
    {
      name: "import alias",
      fixture: { importName: "motionEngine" },
      expected: "GSAP must use the single approved default import"
    },
    {
      name: "GSAP in another file",
      fixture: { filePath: "src/components/rogue.tsx" },
      expected: "GSAP is only approved in src/components/ui.tsx"
    }
  ])("rejects GSAP production-shape bypass: $name", ({ fixture, expected }) => {
    expect(auditGsapMotion(gsapFixture(fixture)).join("\n")).toContain(expected);
  });
});
