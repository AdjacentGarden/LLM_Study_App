import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

const sourceUrl = (path: string) => new URL(`../src/${path}`, import.meta.url);
const main = readFileSync(sourceUrl("main.tsx"), "utf8");
const tools = readFileSync(sourceUrl("demo/AdditionalTools.tsx"), "utf8");
const shell = readFileSync(sourceUrl("components/StudioShell.tsx"), "utf8");
const account = readFileSync(sourceUrl("components/AccountGate.tsx"), "utf8");

// Follow runtime static imports and reexports, including barrels. A new indirect
// eager import must fail the same budget as an import in AdditionalTools itself.
function staticModules(entry: URL, visited = new Set<string>()) {
  if (visited.has(entry.href)) return visited;
  visited.add(entry.href);
  const source = ts.createSourceFile(fileURLToPath(entry), readFileSync(entry, "utf8"), ts.ScriptTarget.Latest, true);
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement) && !ts.isExportDeclaration(statement)) continue;
    if (!statement.moduleSpecifier || !ts.isStringLiteral(statement.moduleSpecifier)) continue;
    if (ts.isExportDeclaration(statement) && (statement.isTypeOnly ||
      (statement.exportClause && ts.isNamedExports(statement.exportClause) && statement.exportClause.elements.every(item => item.isTypeOnly)))) continue;
    if (ts.isImportDeclaration(statement) && statement.importClause) {
      const clause = statement.importClause;
      if (clause.isTypeOnly || (!clause.name && clause.namedBindings && ts.isNamedImports(clause.namedBindings) &&
        clause.namedBindings.elements.every(item => item.isTypeOnly))) continue;
    }
    const specifier = statement.moduleSpecifier.text;
    if (!specifier.startsWith(".") || /\.(css|svg|png|webp|jpg|woff2?)$/.test(specifier)) continue;
    const resolved = [specifier, `${specifier}.ts`, `${specifier}.tsx`, `${specifier}/index.ts`, `${specifier}/index.tsx`]
      .map(path => new URL(path, entry)).find(path => existsSync(path) && statSync(path).isFile());
    assert.ok(resolved, `runtime import ${specifier} in ${fileURLToPath(entry)} must resolve`);
    staticModules(resolved, visited);
  }
  return visited;
}

test("heavy secondary experiences stay outside the demo entry's static JavaScript path", () => {
  assert.match(main, /const DemoDirectEntry = lazy\(\(\) => import\("\.\/demo\/DemoDirectEntry"\)\)/);
  assert.match(shell, /lazy\(\(\) => loadStudio\(\)/);
  assert.match(account, /const RegisterPage = lazy/);
  for (const name of ["SocialPage", "UserProfilePage", "ProfileDashboard"]) {
    assert.match(tools, new RegExp(`const ${name} = lazy\\(\\(\\) => import\\("\\.\\.\\/components\\/${name}"\\)`));
  }
  assert.match(tools, /<Suspense fallback=\{<Card><p role="status">/);
  const initialModules = staticModules(sourceUrl("demo/DemoDirectEntry.tsx"));
  for (const name of ["LearningStudio", "SocialPage", "UserProfilePage", "ProfileDashboard", "RegisterPage"]) {
    assert.ok(!initialModules.has(sourceUrl(`components/${name}.tsx`).href), `${name} must not be statically reachable from the active demo entry`);
  }
});

test("default avatar is small enough for immediate profile rendering", () => {
  const avatar = new URL("../public/assets/brand/profile-avatar-editorial-v3.webp", import.meta.url);
  assert.ok(statSync(avatar).size < 60_000, "default avatar must remain below 60 KB");
});
