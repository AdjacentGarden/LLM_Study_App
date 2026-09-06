import postcss, { type AtRule, type Declaration, type Node, type Rule } from "postcss";
import selectorParser from "postcss-selector-parser";
import valueParser from "postcss-value-parser";
import ts from "typescript";

export type MotionSource = {
  filePath: string;
  sourceText: string;
};

const durationTokens = new Map([
  ["--motion-duration-global", "350ms"],
  ["--motion-duration-global-emphasis", "450ms"],
  ["--motion-duration-local-fast", "150ms"],
  ["--motion-duration-local-base", "180ms"],
  ["--motion-duration-local-slow", "200ms"],
  ["--motion-duration-loading", "1200ms"]
]);

const easingTokens = new Map([
  ["--motion-ease-global-enter", "cubic-bezier(.25, 1, .5, 1)"],
  ["--motion-ease-global-exit", "cubic-bezier(.5, 0, .75, 0)"],
  ["--motion-ease-local-enter", "cubic-bezier(.22, 1, .36, 1)"],
  ["--motion-ease-local-exit", "cubic-bezier(.32, 0, .67, 0)"],
  ["--motion-ease-local-state", "cubic-bezier(.65, 0, .35, 1)"],
  ["--motion-ease-progress", "cubic-bezier(.4, 0, .2, 1)"],
  ["--motion-ease-sprite", "steps(8,end)"]
]);

const loadingAnimations = new Map([
  ["home-loading-shimmer", "linear"],
  ["mistake-loading-pulse", "--motion-ease-local-state"],
  ["motion-spinner", "linear"],
  ["motion-skeleton-pulse", "--motion-ease-local-state"],
  ["processing-cloud-sprite-loading", "--motion-ease-sprite"],
  ["study-loading-shimmer", "linear"]
]);

const animationKeywords = new Set([
  "alternate",
  "alternate-reverse",
  "backwards",
  "both",
  "forwards",
  "normal",
  "paused",
  "reverse",
  "running"
]);

type ParsedAnimation = {
  delay?: string;
  duration?: string;
  easing?: string;
  iteration: string;
  name?: string;
};

type ParsedTransition = {
  delay?: string;
  duration?: string;
  easing?: string;
  property?: string;
};

const conditionalAtRuleNames = new Set(["media", "container", "supports"]);

function conditionalAtRules(node: Node) {
  const conditional: AtRule[] = [];
  let current = node.parent;
  while (current) {
    if (current.type === "atrule" && conditionalAtRuleNames.has((current as AtRule).name.toLowerCase())) {
      conditional.push(current as AtRule);
    }
    current = current.parent;
  }
  return conditional;
}

function ownerRule(declaration: Declaration) {
  return declaration.parent?.type === "rule" ? declaration.parent as Rule : undefined;
}

function isReducedMotion(node: Node) {
  const conditional = conditionalAtRules(node);
  return conditional.length === 1
    && conditional[0].name.toLowerCase() === "media"
    && /^\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)$/i.test(conditional[0].params.trim());
}

function isConditional(node: Node) {
  return conditionalAtRules(node).length > 0;
}

function sourceLocation(filePath: string, node: Node) {
  return `${filePath}:${node.source?.start?.line ?? 1}`;
}

function splitNodesAtComma(nodes: valueParser.Node[]) {
  const items: valueParser.Node[][] = [[]];
  for (const node of nodes) {
    if (node.type === "div" && node.value === ",") items.push([]);
    else items.at(-1)?.push(node);
  }
  return items.map((item) => valueParser.stringify(item).trim());
}

function splitValueList(value: string) {
  return splitNodesAtComma(valueParser(value).nodes);
}

function meaningfulNodes(value: string) {
  return valueParser(value).nodes.filter((node) => node.type !== "space" && node.type !== "comment");
}

function varReference(node: valueParser.Node) {
  if (node.type !== "function" || node.value.toLowerCase() !== "var") return undefined;
  const parts = splitNodesAtComma(node.nodes);
  return { fallback: parts.length > 1, name: parts[0]?.trim() ?? "" };
}

function directVar(value: string) {
  const nodes = meaningfulNodes(value);
  if (nodes.length !== 1) return undefined;
  return varReference(nodes[0]);
}

function timeInMilliseconds(value: string) {
  const match = /^([+]?(?:\d+\.?\d*|\.\d+))(ms|s)$/i.exec(value.trim());
  if (!match) return undefined;
  return Number(match[1]) * (match[2].toLowerCase() === "s" ? 1000 : 1);
}

function selectorBranches(declaration: Declaration) {
  const selector = ownerRule(declaration)?.selector;
  if (!selector) return [];
  try {
    return selectorParser().astSync(selector).nodes;
  } catch {
    return [];
  }
}

function isClass(node: selectorParser.Node, value: string) {
  return node.type === "class" && node.value === value;
}

function isAttribute(node: selectorParser.Node, attribute: string, value: string) {
  return node.type === "attribute"
    && node.attribute === attribute
    && node.operator === "="
    && node.value === value
    && !node.insensitive;
}

function isApprovedSnap(declaration: Declaration) {
  const branches = selectorBranches(declaration);
  return branches.length > 0 && branches.every((selector) => {
    const nodes = selector.nodes;
    return nodes.length === 2
      && isClass(nodes[0], "ai-orb")
      && (isClass(nodes[1], "dragging") || isAttribute(nodes[1], "data-ai-orb-settling", "true"));
  });
}

function isBoundedCardStagger(value: string, declaration: Declaration) {
  const compact = value.toLowerCase().replace(/\s+/g, "");
  const branches = selectorBranches(declaration);
  return compact === "calc(var(--motion-course-card-index)*30ms)"
    && branches.length > 0
    && branches.every((selector) => selector.nodes.length === 1
      && isAttribute(selector.nodes[0], "data-motion-course-card-state", "entering"));
}

function isApprovedTransientWillChange(declaration: Declaration) {
  const branches = selectorBranches(declaration);
  return branches.length > 0 && branches.every((selector) => {
    const nodes = selector.nodes;
    const screenTransition = nodes.length === 4
      && isClass(nodes[0], "motion-screen-transition")
      && isAttribute(nodes[1], "data-motion-state", "transitioning")
      && nodes[2].type === "combinator"
      && nodes[2].value.trim() === ">"
      && isClass(nodes[3], "motion-screen-surface");
    const sharedSurface = nodes.length === 3
      && isClass(nodes[0], "ai-shared-surface")
      && nodes.slice(1).every((node) => node.type === "attribute")
      && nodes.slice(1).some((node) => isAttribute(node, "data-motion-ready", "true"))
      && nodes.slice(1).some((node) => isAttribute(node, "data-motion-state", "entering")
        || isAttribute(node, "data-motion-state", "closing"));
    return screenTransition || sharedSurface;
  });
}

function validateDuration(
  value: string | undefined,
  declaration: Declaration,
  failures: string[],
  options: { loading?: boolean; required?: boolean } = {}
) {
  if (!value) {
    if (options.required) failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} is missing a duration`);
    return undefined;
  }
  const reference = directVar(value);
  if (reference && !reference.fallback && durationTokens.has(reference.name)) {
    if (reference.name === "--motion-duration-loading" && !options.loading) {
      failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} uses the loading duration on finite motion`);
    }
    if (reference.name !== "--motion-duration-loading" && options.loading) {
      failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} loading motion does not use the loading duration`);
    }
    return reference.name;
  }

  const milliseconds = timeInMilliseconds(value);
  const reducedFallback = isReducedMotion(declaration) && milliseconds !== undefined && milliseconds >= 0 && milliseconds <= 1;
  const zeroSnap = milliseconds === 0 && isApprovedSnap(declaration);
  if (!reducedFallback && !zeroSnap) {
    failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} duration must use a semantic token`);
  }
  return milliseconds === undefined ? value : `${milliseconds}ms`;
}

function validateEasing(value: string | undefined, declaration: Declaration, failures: string[], allowLinear = false) {
  if (!value) {
    failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} is missing a semantic easing`);
    return undefined;
  }
  const reference = directVar(value);
  if (reference && !reference.fallback && easingTokens.has(reference.name)) return reference.name;
  if (allowLinear && value.toLowerCase() === "linear") return "linear";
  failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} easing must use a semantic token`);
  return value;
}

function validateVariables(
  declaration: Declaration,
  customProperties: Map<string, Declaration[]>,
  failures: string[]
) {
  const filePath = declaration.source?.input.file ?? "<css>";
  const resolve = (name: string, stack: string[]) => {
    if (stack.includes(name)) {
      failures.push(`${sourceLocation(filePath, declaration)} custom property cycle: ${[...stack, name].join(" -> ")}`);
      return;
    }
    const definitions = customProperties.get(name) ?? [];
    if (definitions.length === 0) {
      failures.push(`${sourceLocation(filePath, declaration)} references unknown custom property ${name}`);
      return;
    }
    if (definitions.length > 1) {
      failures.push(`${sourceLocation(filePath, declaration)} references cascade-dependent custom property ${name}`);
      return;
    }
    valueParser(definitions[0].value).walk((node) => {
      const reference = varReference(node);
      if (reference) resolve(reference.name, [...stack, name]);
    });
  };

  valueParser(declaration.value).walk((node) => {
    const reference = varReference(node);
    if (!reference) return;
    if (reference.fallback) {
      failures.push(`${sourceLocation(filePath, declaration)} var(${reference.name}) must not contain a fallback`);
      return false;
    }
    if (durationTokens.has(reference.name) || easingTokens.has(reference.name)) return;
    if (reference.name === "--motion-course-card-index" && isBoundedCardStagger(declaration.value, declaration)) return;
    resolve(reference.name, []);
    failures.push(`${sourceLocation(filePath, declaration)} custom property ${reference.name} may hide a motion shorthand`);
    return false;
  });
}

function parseAnimation(value: string, declaration: Declaration, failures: string[]): ParsedAnimation | undefined {
  if (value.toLowerCase() === "none") return undefined;
  const result: ParsedAnimation = { iteration: "1" };
  for (const node of meaningfulNodes(value)) {
    const serialized = valueParser.stringify(node).trim();
    const lower = serialized.toLowerCase();
    const reference = varReference(node);
    if (reference) {
      if (!reference.fallback && durationTokens.has(reference.name) && !result.duration) result.duration = serialized;
      else if (!reference.fallback && easingTokens.has(reference.name) && !result.easing) result.easing = serialized;
      else failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} has an ambiguous animation variable`);
      continue;
    }
    if (timeInMilliseconds(serialized) !== undefined) {
      if (!result.duration) result.duration = serialized;
      else if (!result.delay) result.delay = serialized;
      else failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} has too many animation times`);
      continue;
    }
    if (node.type === "function" && ["cubic-bezier", "steps", "linear"].includes(node.value.toLowerCase())) {
      if (result.easing) failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} repeats animation easing`);
      result.easing = serialized;
      continue;
    }
    if (node.type === "function" && node.value.toLowerCase() === "calc") {
      if (result.delay) failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} repeats animation delay`);
      result.delay = serialized;
      continue;
    }
    if (node.type !== "word") {
      failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} contains unsupported animation syntax`);
      continue;
    }
    if (["ease", "ease-in", "ease-out", "ease-in-out", "linear", "step-start", "step-end"].includes(lower)) {
      if (result.easing) failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} repeats animation easing`);
      result.easing = lower;
    } else if (lower === "infinite" || /^\d*\.?\d+$/.test(lower)) {
      result.iteration = lower;
    } else if (!animationKeywords.has(lower)) {
      if (result.name) failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} repeats animation name`);
      result.name = serialized;
    }
  }
  return result;
}

function validateAnimationItem(value: string, declaration: Declaration, failures: string[]) {
  const animation = parseAnimation(value, declaration, failures);
  if (!animation) return;
  const infinite = animation.iteration.toLowerCase() === "infinite";
  if (!animation.name) failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} animation has no explicit name`);
  if (!infinite && animation.iteration !== "1") {
    failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} finite animation iteration-count must be 1`);
  }
  const duration = validateDuration(animation.duration, declaration, failures, { loading: infinite, required: true });
  const easing = validateEasing(animation.easing, declaration, failures, infinite);
  if (animation.delay) {
    const delayMs = timeInMilliseconds(animation.delay);
    if (delayMs !== 0 && !isBoundedCardStagger(animation.delay, declaration)) {
      failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} animation delay is not an approved stagger`);
    }
  }
  if (infinite) {
    const expectedEasing = animation.name ? loadingAnimations.get(animation.name) : undefined;
    if (!expectedEasing || duration !== "--motion-duration-loading" || easing !== expectedEasing) {
      failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} infinite animation is not on the loading allowlist`);
    }
  }

  if (isReducedMotion(declaration)) {
    failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} reduced motion must disable animation instead of retiming it`);
  }
}

function parseTransition(value: string, declaration: Declaration, failures: string[]): ParsedTransition | undefined {
  if (value.toLowerCase() === "none") return undefined;
  const result: ParsedTransition = {};
  for (const node of meaningfulNodes(value)) {
    const serialized = valueParser.stringify(node).trim();
    const lower = serialized.toLowerCase();
    const reference = varReference(node);
    if (reference) {
      if (!reference.fallback && durationTokens.has(reference.name) && !result.duration) result.duration = serialized;
      else if (!reference.fallback && easingTokens.has(reference.name) && !result.easing) result.easing = serialized;
      else failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} has an ambiguous transition variable`);
      continue;
    }
    if (timeInMilliseconds(serialized) !== undefined) {
      if (!result.duration) result.duration = serialized;
      else if (!result.delay) result.delay = serialized;
      else failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} has too many transition times`);
      continue;
    }
    if (node.type === "function" && ["cubic-bezier", "steps", "linear"].includes(node.value.toLowerCase())) {
      result.easing = serialized;
      continue;
    }
    if (node.type !== "word") {
      failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} contains unsupported transition syntax`);
      continue;
    }
    if (["ease", "ease-in", "ease-out", "ease-in-out", "linear", "step-start", "step-end"].includes(lower)) {
      result.easing = lower;
    } else if (!result.property) result.property = lower;
    else failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} repeats transition property`);
  }
  return result;
}

function validateTransitionItem(value: string, declaration: Declaration, failures: string[]) {
  const transition = parseTransition(value, declaration, failures);
  if (!transition) return;
  if (!transition.property || transition.property === "all") {
    failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} transition must name a non-all property`);
  }
  validateDuration(transition.duration, declaration, failures, { required: true });
  validateEasing(transition.easing, declaration, failures);
  if (transition.delay && timeInMilliseconds(transition.delay) !== 0) {
    failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} transition delay is not allowed`);
  }
  if (isReducedMotion(declaration)) {
    const duration = transition.duration ? timeInMilliseconds(transition.duration) : undefined;
    if (duration === undefined || duration > 1) {
      failures.push(`${sourceLocation(declaration.source?.input.file ?? "<css>", declaration)} reduced transition must be none or at most 1ms`);
    }
  }
}

function validateLonghandLists(rule: Rule, failures: string[]) {
  const declarations = new Map<string, Declaration>();
  rule.walkDecls((declaration) => {
    const property = declaration.prop.toLowerCase();
    if (/^(?:animation|transition)-/.test(property)) declarations.set(property, declaration);
  });
  const iteration = declarations.get("animation-iteration-count");
  const anchor = iteration
    ?? declarations.get("animation-name")
    ?? declarations.get("animation-duration")
    ?? declarations.get("animation-timing-function");
  if (!anchor) return;
  const iterations = splitValueList(iteration?.value ?? "1").map((item) => item.toLowerCase());
  const names = splitValueList(declarations.get("animation-name")?.value ?? "none");
  const durations = splitValueList(declarations.get("animation-duration")?.value ?? "0s");
  const easings = splitValueList(declarations.get("animation-timing-function")?.value ?? "ease");
  const count = Math.max(iterations.length, names.length, durations.length, easings.length);
  for (let index = 0; index < count; index += 1) {
    const currentIteration = iterations[index % iterations.length];
    const currentDuration = durations[index % durations.length];
    const currentEasing = easings[index % easings.length];
    if (currentIteration === "1") {
      if (directVar(currentDuration)?.name === "--motion-duration-loading") {
        failures.push(`${sourceLocation(anchor.source?.input.file ?? "<css>", anchor)} uses the loading duration on finite motion`);
      }
      if (currentEasing.trim().toLowerCase() === "linear") {
        failures.push(`${sourceLocation(anchor.source?.input.file ?? "<css>", anchor)} easing must use a semantic token`);
      }
      continue;
    }
    if (currentIteration !== "infinite") {
      failures.push(`${sourceLocation(anchor.source?.input.file ?? "<css>", anchor)} animation iteration-count must be 1 or approved infinite`);
      continue;
    }
    const name = names[index % names.length];
    const duration = directVar(currentDuration);
    const easingValue = currentEasing;
    const easing = directVar(easingValue)?.name ?? easingValue.toLowerCase();
    if (!loadingAnimations.has(name)
      || duration?.name !== "--motion-duration-loading"
      || loadingAnimations.get(name) !== easing) {
      failures.push(`${sourceLocation(anchor.source?.input.file ?? "<css>", anchor)} longhand infinite animation is not approved`);
    }
  }
}

export function auditCssMotion(sources: MotionSource[]) {
  const failures: string[] = [];
  const roots = sources.map((source) => ({
    ...source,
    root: postcss.parse(source.sourceText, { from: source.filePath })
  }));
  const customProperties = new Map<string, Declaration[]>();
  for (const { root } of roots) {
    root.walkDecls((declaration) => {
      if (!declaration.prop.startsWith("--")) return;
      const definitions = customProperties.get(declaration.prop) ?? [];
      definitions.push(declaration);
      customProperties.set(declaration.prop, definitions);
    });
  }

  for (const [token, expectedValue] of [...durationTokens, ...easingTokens]) {
    const definitions = (customProperties.get(token) ?? []).filter((declaration) => !isConditional(declaration));
    if (definitions.length !== 1 || definitions[0].value !== expectedValue) {
      failures.push(`${token} must have exactly one canonical ${expectedValue} definition`);
    }
  }

  for (const { root } of roots) {
    root.walkRules((rule) => validateLonghandLists(rule, failures));
    root.walkDecls((declaration) => {
      const property = declaration.prop.toLowerCase();
      const filePath = declaration.source?.input.file ?? "<css>";

      if (declaration.prop.startsWith("--motion-duration-") && isConditional(declaration)) {
        const milliseconds = timeInMilliseconds(declaration.value);
        if (!isReducedMotion(declaration) || milliseconds === undefined || milliseconds < 0 || milliseconds > 1) {
          failures.push(`${sourceLocation(filePath, declaration)} conditional rule redefines a motion duration outside the exact reduced 0-1ms allowance`);
        }
      }
      if (declaration.prop.startsWith("--motion-ease-") && isConditional(declaration)) {
        failures.push(`${sourceLocation(filePath, declaration)} conditional rule must not redefine motion easing`);
      }

      if (property === "will-change" && declaration.value.trim().toLowerCase() !== "auto") {
        if (!isApprovedTransientWillChange(declaration)) {
          failures.push(`${sourceLocation(filePath, declaration)} enables permanent will-change`);
        }
      }

      if (!/^(?:animation|transition)(?:-|$)/.test(property)) return;
      if (isConditional(declaration) && !isReducedMotion(declaration) && property !== "animation-name") {
        failures.push(`${sourceLocation(filePath, declaration)} conditional rule introduces a timing override`);
      }
      validateVariables(declaration, customProperties, failures);

      if (property === "animation") {
        for (const item of splitValueList(declaration.value)) validateAnimationItem(item, declaration, failures);
      } else if (property === "transition") {
        for (const item of splitValueList(declaration.value)) validateTransitionItem(item, declaration, failures);
      } else if (property === "animation-duration" || property === "transition-duration") {
        for (const item of splitValueList(declaration.value)) {
          const loadingLonghand = property === "animation-duration"
            && directVar(item)?.name === "--motion-duration-loading";
          validateDuration(item, declaration, failures, { loading: loadingLonghand });
        }
      } else if (property === "animation-timing-function" || property === "transition-timing-function") {
        for (const item of splitValueList(declaration.value)) validateEasing(item, declaration, failures, property === "animation-timing-function");
      } else if (property === "animation-delay" || property === "transition-delay") {
        for (const item of splitValueList(declaration.value)) {
          const milliseconds = timeInMilliseconds(item);
          if (milliseconds !== 0 && !(property === "animation-delay" && isBoundedCardStagger(item, declaration))) {
            failures.push(`${sourceLocation(filePath, declaration)} has an unapproved delay`);
          }
        }
      } else if (property === "transition-property") {
        for (const item of splitValueList(declaration.value)) {
          if (item.trim().toLowerCase() === "all" || directVar(item)) {
            failures.push(`${sourceLocation(filePath, declaration)} transition-property must not be all or variable`);
          }
        }
      } else if (property === "animation-iteration-count") {
        for (const item of splitValueList(declaration.value)) {
          if (!/^(?:1|infinite)$/i.test(item.trim())) {
            failures.push(`${sourceLocation(filePath, declaration)} animation iteration-count is not approved`);
          }
        }
      } else if (property === "animation-name") {
        for (const item of splitValueList(declaration.value)) {
          if (directVar(item)) failures.push(`${sourceLocation(filePath, declaration)} animation-name must not be variable`);
        }
      }
    });
  }
  return [...new Set(failures)];
}

function isGsapPackageSpecifier(value: string) {
  const normalized = value.replace(/\\/g, "/").toLowerCase();
  return normalized === "gsap" || normalized.startsWith("gsap/");
}

function gsapPackageSpecifier(node: ts.Node) {
  return (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
    && isGsapPackageSpecifier(node.text)
    ? node.text
    : undefined;
}

export function auditGsapMotion(sources: MotionSource[]) {
  const failures: string[] = [];
  const inventory = { imports: 0, registerPlugin: 0, killTweensOf: 0, set: 0, to: 0 };
  let hasGsapSyntax = false;

  for (const source of sources) {
    const sourceFile = ts.createSourceFile(
      source.filePath,
      source.sourceText,
      ts.ScriptTarget.Latest,
      true,
      source.filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS
    );
    const location = (node: ts.Node) => {
      const line = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
      return `${source.filePath}:${line}`;
    };

    const gsapImports = sourceFile.statements.filter((statement): statement is ts.ImportDeclaration & { moduleSpecifier: ts.StringLiteral } => (
      ts.isImportDeclaration(statement)
      && ts.isStringLiteral(statement.moduleSpecifier)
      && isGsapPackageSpecifier(statement.moduleSpecifier.text)
    ));
    let sourceHasGsapSyntax = gsapImports.length > 0;
    const findGsapSyntax = (node: ts.Node) => {
      if (gsapPackageSpecifier(node) !== undefined
        || (ts.isIdentifier(node) && node.text === "gsap")) {
        sourceHasGsapSyntax = true;
      }
      ts.forEachChild(node, findGsapSyntax);
    };
    findGsapSyntax(sourceFile);
    if (!sourceHasGsapSyntax) continue;
    hasGsapSyntax = true;
    const normalizedPath = source.filePath.replace(/\\/g, "/");
    if (!normalizedPath.endsWith("src/components/ui.tsx")) {
      failures.push(`${source.filePath}: GSAP is only approved in src/components/ui.tsx`);
    }
    inventory.imports += gsapImports.length;
    const approvedImport = gsapImports.length === 1
      && gsapImports[0].moduleSpecifier.text === "gsap"
      && gsapImports[0].importClause?.name?.text === "gsap"
      && !gsapImports[0].importClause?.namedBindings
      && !gsapImports[0].importClause?.isTypeOnly;
    if (!approvedImport) failures.push(`${source.filePath}: GSAP must use the single approved default import`);
    for (const declaration of gsapImports) {
      if (declaration.moduleSpecifier.text !== "gsap") {
        failures.push(`${location(declaration.moduleSpecifier)} GSAP package entry "${declaration.moduleSpecifier.text}" is not allowed; only import gsap from "gsap" is approved`);
      }
    }

    const auditObject = (
      expression: ts.Expression | undefined,
      call: ts.CallExpression,
      expectedKeys: string[],
      expectedValues: Record<string, string>
    ) => {
      if (!expression || !ts.isObjectLiteralExpression(expression)) {
        failures.push(`${location(call)} GSAP vars must be an inline object literal`);
        return;
      }
      if (expression.properties.some((property) => ts.isSpreadAssignment(property))) {
        failures.push(`${location(call)} GSAP vars must not contain spread`);
      }
      const assignments = expression.properties.filter((property): property is ts.PropertyAssignment => ts.isPropertyAssignment(property));
      if (assignments.length !== expression.properties.length
        || assignments.some((property) => !ts.isIdentifier(property.name))) {
        failures.push(`${location(call)} GSAP vars require direct identifier properties`);
        return;
      }
      const keys = assignments.map((property) => (property.name as ts.Identifier).text);
      if (keys.join(",") !== expectedKeys.join(",")) {
        failures.push(`${location(call)} GSAP vars keys must exactly be ${expectedKeys.join(",")}`);
      }
      for (const property of assignments) {
        const key = (property.name as ts.Identifier).text;
        if (expectedValues[key] !== undefined && property.initializer.getText(sourceFile) !== expectedValues[key]) {
          failures.push(`${location(call)} GSAP ${key} must be ${expectedValues[key]}`);
        }
      }
    };

    const visit = (node: ts.Node) => {
      const packageEntry = gsapPackageSpecifier(node);
      if (packageEntry !== undefined
        && !(ts.isImportDeclaration(node.parent) && node.parent.moduleSpecifier === node)) {
        failures.push(`${location(node)} indirect GSAP lookup is not allowed: ${packageEntry}`);
      }
      if (ts.isIdentifier(node) && node.text === "gsap") {
        const importBinding = ts.isImportClause(node.parent) && node.parent.name === node;
        const member = ts.isPropertyAccessExpression(node.parent)
          && node.parent.expression === node
          && !node.parent.questionDotToken;
        const directCall = member
          && ts.isCallExpression(node.parent.parent)
          && node.parent.parent.expression === node.parent;
        if (!importBinding && !directCall) {
          failures.push(`${location(node)} GSAP object and methods must not escape direct calls`);
        }
      }
      if (ts.isCallExpression(node)
        && ts.isPropertyAccessExpression(node.expression)
        && ts.isIdentifier(node.expression.expression)
        && node.expression.expression.text === "gsap") {
        const method = node.expression.name.text;
        if (!normalizedPath.endsWith("src/components/ui.tsx")) {
          failures.push(`${location(node)} GSAP call is outside the approved file`);
        }
        if (method === "registerPlugin") {
          inventory.registerPlugin += 1;
          if (node.arguments.length !== 1 || node.arguments[0].getText(sourceFile) !== "useGSAP") {
            failures.push(`${location(node)} gsap.registerPlugin shape is not approved`);
          }
        } else if (method === "killTweensOf") {
          inventory.killTweensOf += 1;
          if (node.arguments.length !== 1 || node.arguments[0].getText(sourceFile) !== "selection") {
            failures.push(`${location(node)} gsap.killTweensOf shape is not approved`);
          }
        } else if (method === "set") {
          inventory.set += 1;
          if (node.arguments.length !== 2 || node.arguments[0].getText(sourceFile) !== "selection") {
            failures.push(`${location(node)} gsap.set call shape is not approved`);
          }
          const object = node.arguments[1];
          const keys = ts.isObjectLiteralExpression(object)
            ? object.properties.filter((property): property is ts.PropertyAssignment => ts.isPropertyAssignment(property) && ts.isIdentifier(property.name))
              .map((property) => (property.name as ts.Identifier).text)
            : [];
          const wide = keys.join(",") === "x,y,width,height";
          auditObject(object, node, wide ? ["x", "y", "width", "height"] : ["width", "height"], {
            x: "targetPosition.x",
            y: "targetPosition.y",
            width: "targetPosition.width",
            height: "targetPosition.height"
          });
        } else if (method === "to") {
          inventory.to += 1;
          if (node.arguments.length !== 2 || node.arguments[0].getText(sourceFile) !== "selection") {
            failures.push(`${location(node)} gsap.to call shape is not approved`);
          }
          auditObject(node.arguments[1], node, ["x", "y", "width", "height", "duration", "ease", "overwrite"], {
            x: "targetPosition.x",
            y: "targetPosition.y",
            width: "targetPosition.width",
            height: "targetPosition.height",
            duration: "localSlowMotionDurationSeconds",
            ease: "localStateGsapEase",
            overwrite: '"auto"'
          });
        } else {
          failures.push(`${location(node)} GSAP API ${method} is not on the production allowlist`);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sourceFile);
  }

  if (hasGsapSyntax && (inventory.imports !== 1
    || inventory.registerPlugin !== 1
    || inventory.killTweensOf !== 1
    || inventory.set !== 2
    || inventory.to !== 1)) {
    failures.push(`GSAP production inventory must be import=1, registerPlugin=1, killTweensOf=1, set=2, to=1; received ${JSON.stringify(inventory)}`);
  }
  return [...new Set(failures)];
}

export const semanticMotionTokenFixture = `
:root {
  --motion-duration-global: 350ms;
  --motion-duration-global-emphasis: 450ms;
  --motion-duration-local-fast: 150ms;
  --motion-duration-local-base: 180ms;
  --motion-duration-local-slow: 200ms;
  --motion-duration-loading: 1200ms;
  --motion-ease-global-enter: cubic-bezier(.25, 1, .5, 1);
  --motion-ease-global-exit: cubic-bezier(.5, 0, .75, 0);
  --motion-ease-local-enter: cubic-bezier(.22, 1, .36, 1);
  --motion-ease-local-exit: cubic-bezier(.32, 0, .67, 0);
  --motion-ease-local-state: cubic-bezier(.65, 0, .35, 1);
  --motion-ease-progress: cubic-bezier(.4, 0, .2, 1);
  --motion-ease-sprite: steps(8,end);
}
`;
