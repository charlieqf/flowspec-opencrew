#!/usr/bin/env node
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";

const traverse = traverseModule.default || traverseModule;

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(SCRIPT_DIR, "..");
const REPO_ROOT = resolve(FRONTEND_ROOT, "..");
const CONTROLLER_PATH = resolve(FRONTEND_ROOT, "src/shell/useOpenCrewAppController.jsx");
const CONTROLLERS_DIR = resolve(FRONTEND_ROOT, "src/shell/controllers");
const SHELL_VIEW_PATH = resolve(FRONTEND_ROOT, "src/shell/OpenCrewShellView.jsx");
const SNAPSHOT_PATH = resolve(FRONTEND_ROOT, "src/shell/__contracts__/useOpenCrewAppController.return-surface.json");
const REPORT_PATH = resolve(REPO_ROOT, "docs/opencrew_g1d_controller_dependency_report_2026-07-06.md");
const CONTROLLER_FILE_RE = /\.[jt]sx?$/;

const SKIP_KEYS = new Set([
  "loc",
  "start",
  "end",
  "range",
  "leadingComments",
  "innerComments",
  "trailingComments",
  "extra",
]);

function rel(path) {
  return relative(REPO_ROOT, path);
}

function readSource(path) {
  return readFileSync(path, "utf8");
}

function parseSource(source) {
  return parse(source, {
    sourceType: "module",
    plugins: ["jsx", "typescript"],
  });
}

function isNode(value) {
  return Boolean(value && typeof value === "object" && typeof value.type === "string");
}

function isFunctionNode(node) {
  return [
    "ArrowFunctionExpression",
    "FunctionDeclaration",
    "FunctionExpression",
    "ObjectMethod",
    "ClassMethod",
  ].includes(node?.type);
}

function walkNode(node, visitor, options = {}, isRoot = true) {
  if (!isNode(node)) return;
  if (visitor(node) === false) return;
  if (!isRoot && options.skipNestedFunctions && isFunctionNode(node)) return;
  for (const [key, value] of Object.entries(node)) {
    if (SKIP_KEYS.has(key)) continue;
    if (Array.isArray(value)) {
      for (const item of value) walkNode(item, visitor, options, false);
    } else {
      walkNode(value, visitor, options, false);
    }
  }
}

function uniqueSorted(items) {
  return Array.from(new Set(items)).sort();
}

function identifierName(node) {
  return node?.type === "Identifier" ? node.name : null;
}

function calleeIdentifierName(node) {
  if (node?.type === "Identifier") return node.name;
  return null;
}

function callNamesInNode(node, names) {
  const allowed = new Set(names);
  const result = [];
  walkNode(node, (item) => {
    if (item.type !== "CallExpression" && item.type !== "OptionalCallExpression") return;
    const name = calleeIdentifierName(item.callee);
    if (name && allowed.has(name)) result.push(name);
  }, { skipNestedFunctions: true });
  return uniqueSorted(result);
}

function untrackReadsInNode(node) {
  const result = [];
  walkNode(node, (item) => {
    if (item.type !== "CallExpression" && item.type !== "OptionalCallExpression") return;
    if (calleeIdentifierName(item.callee) !== "untrack") return;
    const firstArg = item.arguments?.[0];
    if (firstArg?.type === "Identifier") result.push(firstArg.name);
  }, { skipNestedFunctions: true });
  return uniqueSorted(result);
}

function getControllerFunction(ast) {
  let controller = null;
  traverse(ast, {
    FunctionDeclaration(path) {
      if (path.node.id?.name === "useOpenCrewAppController") {
        controller = path.node;
        path.stop();
      }
    },
  });
  assert.ok(controller, "useOpenCrewAppController function not found");
  return controller;
}

function getExtractedControllerFunction(ast, sourcePath) {
  let controller = null;
  traverse(ast, {
    FunctionDeclaration(path) {
      const name = path.node.id?.name ?? "";
      if (!name.startsWith("use") || !name.endsWith("Controller")) return;
      controller = path.node;
      path.stop();
    },
  });
  assert.ok(controller, `${rel(sourcePath)} controller function not found`);
  return controller;
}

function getFunctionByName(ast, name) {
  let found = null;
  traverse(ast, {
    FunctionDeclaration(path) {
      if (path.node.id?.name === name) {
        found = path.node;
        path.stop();
      }
    },
  });
  assert.ok(found, `${name} function not found`);
  return found;
}

function topLevelVariableDeclarators(functionNode) {
  const result = [];
  for (const statement of functionNode.body.body) {
    if (statement.type !== "VariableDeclaration") continue;
    result.push(...statement.declarations);
  }
  return result;
}

function extractReturnSurface(functionNode) {
  const statements = functionNode.body.body;
  const returnStatement = [...statements].reverse().find((statement) => statement.type === "ReturnStatement");
  assert.ok(returnStatement?.argument?.type === "ObjectExpression", "controller top-level return object not found");
  const keys = returnStatement.argument.properties.map((property) => {
    assert.equal(property.type, "ObjectProperty", `Unsupported return property type: ${property.type}`);
    const key = identifierName(property.key);
    assert.ok(key, "Only identifier return keys are supported");
    return key;
  });
  return {
    line: returnStatement.loc.start.line,
    key_count: keys.length,
    keys,
  };
}

function extractPropsDestructure(functionNode, componentName) {
  for (const statement of functionNode.body.body) {
    if (statement.type !== "VariableDeclaration") continue;
    for (const declaration of statement.declarations) {
      if (declaration.id.type !== "ObjectPattern") continue;
      if (declaration.init?.type !== "Identifier" || declaration.init.name !== "props") continue;
      return declaration.id.properties.map((property) => {
        assert.equal(property.type, "ObjectProperty", `Unsupported ${componentName} prop pattern: ${property.type}`);
        const key = identifierName(property.key);
        assert.ok(key, `Unsupported ${componentName} prop key`);
        return key;
      });
    }
  }
  throw new Error(`${componentName} props destructure not found`);
}

function extractSignals(functionNode) {
  const signals = [];
  for (const declaration of topLevelVariableDeclarators(functionNode)) {
    if (declaration.id.type !== "ArrayPattern") continue;
    if (declaration.init?.type !== "CallExpression" || identifierName(declaration.init.callee) !== "createSignal") continue;
    const [accessor, setter] = declaration.id.elements;
    if (accessor?.type === "Identifier" && setter?.type === "Identifier") {
      signals.push({ name: accessor.name, setter: setter.name, line: declaration.loc.start.line });
    }
  }
  return signals;
}

function extractMemos(functionNode) {
  const memos = [];
  for (const declaration of topLevelVariableDeclarators(functionNode)) {
    if (declaration.id.type !== "Identifier") continue;
    if (declaration.init?.type !== "CallExpression" || identifierName(declaration.init.callee) !== "createMemo") continue;
    memos.push({ name: declaration.id.name, line: declaration.loc.start.line });
  }
  return memos;
}

function functionBodyNode(node) {
  if (node.type === "FunctionDeclaration") return node.body;
  if (node.type === "FunctionExpression") return node.body;
  if (node.type === "ArrowFunctionExpression") return node.body;
  throw new Error(`Unsupported function-like node: ${node.type}`);
}

function extractNamedFunctions(functionNode) {
  const functions = new Map();
  walkNode(functionNode.body, (node) => {
    if (node.type === "VariableDeclarator" && node.id.type === "Identifier") {
      if (node.init?.type === "ArrowFunctionExpression" || node.init?.type === "FunctionExpression") {
        functions.set(node.id.name, {
          name: node.id.name,
          line: node.loc.start.line,
          bodyNode: functionBodyNode(node.init),
        });
      }
      return;
    }
    if (node.type === "FunctionDeclaration" && node.id?.name) {
      functions.set(node.id.name, {
        name: node.id.name,
        line: node.loc.start.line,
        bodyNode: node.body,
      });
    }
  }, { skipNestedFunctions: false });
  return functions;
}

function extractCreateEffects(functionNode) {
  const effects = [];
  walkNode(functionNode.body, (node) => {
    if (node.type !== "CallExpression") return;
    if (identifierName(node.callee) !== "createEffect") return;
    const callback = node.arguments?.[0];
    if (!callback || (callback.type !== "ArrowFunctionExpression" && callback.type !== "FunctionExpression")) return;
    effects.push({
      line: node.loc.start.line,
      bodyNode: functionBodyNode(callback),
    });
  }, { skipNestedFunctions: true });
  return effects;
}

function transitiveCalls(helperName, helpers, seen = new Set()) {
  if (seen.has(helperName)) return [];
  seen.add(helperName);
  const helper = helpers.get(helperName);
  if (!helper) return [];
  const helperNames = Array.from(helpers.keys()).filter((name) => name !== helperName);
  const direct = callNamesInNode(helper.bodyNode, helperNames);
  const nested = direct.flatMap((name) => transitiveCalls(name, helpers, seen));
  return uniqueSorted([...direct, ...nested]);
}

function transitiveReads(helperName, helpers, accessorNames, seen = new Set()) {
  if (seen.has(helperName)) return [];
  seen.add(helperName);
  const helper = helpers.get(helperName);
  if (!helper) return [];
  const helperNames = Array.from(helpers.keys()).filter((name) => name !== helperName);
  const direct = callNamesInNode(helper.bodyNode, accessorNames);
  const nested = callNamesInNode(helper.bodyNode, helperNames)
    .flatMap((name) => transitiveReads(name, helpers, accessorNames, seen));
  return uniqueSorted([...direct, ...nested]);
}

function extractEventHandlers(functionNode, helpers, accessorNames) {
  const helperNames = Array.from(helpers.keys());
  const handlers = [];
  walkNode(functionNode.body, (node) => {
    if (node.type !== "CallExpression" && node.type !== "OptionalCallExpression") return;
    const callee = node.callee;
    if (callee?.type !== "MemberExpression" && callee?.type !== "OptionalMemberExpression") return;
    if (identifierName(callee.property) !== "addEventListener") return;
    const eventArg = node.arguments?.[0];
    const handlerArg = node.arguments?.[1];
    if (eventArg?.type !== "StringLiteral" || handlerArg?.type !== "Identifier") return;
    const handler = helpers.get(handlerArg.name);
    const directHelperCalls = handler
      ? callNamesInNode(handler.bodyNode, helperNames.filter((name) => name !== handler.name))
      : [];
    const allHelperCalls = uniqueSorted([
      ...directHelperCalls,
      ...directHelperCalls.flatMap((name) => transitiveCalls(name, helpers)),
    ]);
    handlers.push({
      event: eventArg.value,
      handler: handlerArg.name,
      line: node.loc.start.line,
      handler_line: handler?.line ?? null,
      non_reactive_reads: handler ? callNamesInNode(handler.bodyNode, accessorNames) : [],
      helper_calls: directHelperCalls,
      transitive_helper_calls: allHelperCalls,
      transitive_non_reactive_reads: uniqueSorted(allHelperCalls.flatMap((name) => transitiveReads(name, helpers, accessorNames))),
    });
  }, { skipNestedFunctions: false });
  return handlers;
}

function analyzeControllerSource(sourcePath, source, controller, extraAccessorNames = []) {
  const surface = extractReturnSurface(controller);
  const signals = extractSignals(controller);
  const memos = extractMemos(controller);
  const accessorNames = uniqueSorted([...signals.map((item) => item.name), ...memos.map((item) => item.name), ...extraAccessorNames]);
  const helpers = extractNamedFunctions(controller);
  const helperNames = Array.from(helpers.keys());
  const effects = extractCreateEffects(controller).map((effect) => {
    const helperCalls = callNamesInNode(effect.bodyNode, helperNames);
    return {
      line: effect.line,
      direct_reads: callNamesInNode(effect.bodyNode, accessorNames),
      helper_calls: helperCalls,
      transitive_helper_calls: uniqueSorted(helperCalls.flatMap((name) => transitiveCalls(name, helpers))),
      transitive_tracked_reads: uniqueSorted(helperCalls.flatMap((name) => transitiveReads(name, helpers, accessorNames))),
      untrack_reads: untrackReadsInNode(effect.bodyNode),
    };
  });
  const eventHandlers = extractEventHandlers(controller, helpers, accessorNames);

  return {
    source: rel(sourcePath),
    name: controller.id?.name ?? "(anonymous)",
    source_stats: {
      controller_lines: source.split(/\r?\n/).length,
      signals: signals.length,
      memos: memos.length,
      helpers: helpers.size,
      create_effects: effects.length,
      returned_keys: surface.key_count,
    },
    return_surface: surface,
    signals,
    memos,
    effects,
    event_handlers: eventHandlers,
  };
}

function listExtractedControllerSources() {
  if (!existsSync(CONTROLLERS_DIR)) return [];
  return readdirSync(CONTROLLERS_DIR)
    .filter((name) => CONTROLLER_FILE_RE.test(name))
    .sort()
    .map((name) => {
      const path = resolve(CONTROLLERS_DIR, name);
      return { path, source: readSource(path) };
    });
}

function analyzeExtractedController({ path, source }) {
  const ast = parseSource(source);
  return analyzeControllerSource(path, source, getExtractedControllerFunction(ast, path));
}

function buildInventory(controllerSource, shellViewSource, extractedControllerSources = []) {
  const controllerAst = parseSource(controllerSource);
  const shellViewAst = parseSource(shellViewSource);
  const controller = getControllerFunction(controllerAst);
  const shellView = getFunctionByName(shellViewAst, "OpenCrewShellView");
  const shellViewProps = extractPropsDestructure(shellView, "OpenCrewShellView");
  const extractedControllers = extractedControllerSources.map(analyzeExtractedController);
  const extractedAccessorNames = extractedControllers.flatMap((item) => [
    ...item.signals.map((signal) => signal.name),
    ...item.memos.map((memo) => memo.name),
  ]);
  const root = analyzeControllerSource(CONTROLLER_PATH, controllerSource, controller, extractedAccessorNames);

  return {
    controller: root.source,
    shell_view: rel(SHELL_VIEW_PATH),
    source_stats: {
      ...root.source_stats,
      extracted_controllers: extractedControllers.length,
      extracted_controller_lines: extractedControllers.reduce((total, item) => total + item.source_stats.controller_lines, 0),
      extracted_controller_signals: extractedControllers.reduce((total, item) => total + item.source_stats.signals, 0),
      extracted_controller_memos: extractedControllers.reduce((total, item) => total + item.source_stats.memos, 0),
      extracted_controller_helpers: extractedControllers.reduce((total, item) => total + item.source_stats.helpers, 0),
      extracted_controller_create_effects: extractedControllers.reduce((total, item) => total + item.source_stats.create_effects, 0),
      extracted_controller_returned_keys: extractedControllers.reduce((total, item) => total + item.return_surface.key_count, 0),
      shell_view_top_level_props: shellViewProps.length,
    },
    return_surface: root.return_surface,
    shell_view_top_level_props: shellViewProps,
    signals: root.signals,
    memos: root.memos,
    effects: root.effects,
    event_handlers: root.event_handlers,
    extracted_controllers: extractedControllers,
    explicit_g1d_boundaries: {
      root_current_owner: "useOpenCrewAppController owns auth/bootstrap and wires routing callbacks into metering/session controllers.",
      extracted_owners: extractedControllers.map((item) => item.name),
      controller_import_rule: "Extracted shell controllers do not import each other; useOpenCrewAppController wires their dependencies and returned keys.",
    },
  };
}

function snapshotFromInventory(inventory) {
  return {
    source: inventory.controller,
    key_count: inventory.return_surface.key_count,
    keys: inventory.return_surface.keys,
    extracted_controller_surfaces: inventory.extracted_controllers.map((controller) => ({
      source: controller.source,
      name: controller.name,
      key_count: controller.return_surface.key_count,
      keys: controller.return_surface.keys,
    })),
  };
}

function writeJson(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`);
}

function bulletList(items) {
  return items.length ? items.map((item) => `  - ${item}`).join("\n") : "  - none";
}

function pushEffectReport(lines, sourceInventory) {
  lines.push(`### ${sourceInventory.name}`);
  lines.push("");
  lines.push(`- Source: \`${sourceInventory.source}\``);
  if (!sourceInventory.effects.length) {
    lines.push("- `createEffect`: none");
    lines.push("");
    return;
  }
  for (const effect of sourceInventory.effects) {
    lines.push(`- createEffect line ${effect.line}`);
    lines.push("  - Direct reads:");
    lines.push(bulletList(effect.direct_reads));
    lines.push("  - Direct helper calls:");
    lines.push(bulletList(effect.helper_calls));
    lines.push("  - Transitive helper calls:");
    lines.push(bulletList(effect.transitive_helper_calls));
    lines.push("  - Transitive tracked reads through helpers:");
    lines.push(bulletList(effect.transitive_tracked_reads));
    lines.push("  - Explicit `untrack` reads:");
    lines.push(bulletList(effect.untrack_reads));
  }
  lines.push("");
}

function pushEventHandlerReport(lines, sourceInventory) {
  lines.push(`### ${sourceInventory.name}`);
  lines.push("");
  lines.push(`- Source: \`${sourceInventory.source}\``);
  if (!sourceInventory.event_handlers.length) {
    lines.push("- Event handlers: none");
    lines.push("");
    return;
  }
  for (const handler of sourceInventory.event_handlers) {
    lines.push(`- ${handler.event} -> \`${handler.handler}\` at line ${handler.line}`);
    lines.push(`  - handler line: ${handler.handler_line ?? "unknown"}`);
    lines.push(`  - non-reactive reads: ${handler.non_reactive_reads.length ? handler.non_reactive_reads.join(", ") : "none"}`);
    lines.push(`  - direct helper calls: ${handler.helper_calls.length ? handler.helper_calls.join(", ") : "none"}`);
    lines.push(`  - transitive helper calls: ${handler.transitive_helper_calls.length ? handler.transitive_helper_calls.join(", ") : "none"}`);
    lines.push(`  - transitive non-reactive reads: ${handler.transitive_non_reactive_reads.length ? handler.transitive_non_reactive_reads.join(", ") : "none"}`);
  }
  lines.push("");
}

function pushSignalInventory(lines, sourceInventory) {
  lines.push(`### ${sourceInventory.name}`);
  lines.push("");
  lines.push(`- Source: \`${sourceInventory.source}\``);
  if (!sourceInventory.signals.length) {
    lines.push("- Signals: none");
    lines.push("");
    return;
  }
  for (const signal of sourceInventory.signals) {
    lines.push(`- line ${signal.line}: \`${signal.name}\` / \`${signal.setter}\``);
  }
  lines.push("");
}

function pushMemoInventory(lines, sourceInventory) {
  lines.push(`### ${sourceInventory.name}`);
  lines.push("");
  lines.push(`- Source: \`${sourceInventory.source}\``);
  if (!sourceInventory.memos.length) {
    lines.push("- Memos: none");
    lines.push("");
    return;
  }
  for (const memo of sourceInventory.memos) {
    lines.push(`- line ${memo.line}: \`${memo.name}\``);
  }
  lines.push("");
}

function renderReport(inventory) {
  const lines = [];
  const allControllers = [
    {
      source: inventory.controller,
      name: "useOpenCrewAppController",
      source_stats: {
        controller_lines: inventory.source_stats.controller_lines,
        signals: inventory.source_stats.signals,
        memos: inventory.source_stats.memos,
        helpers: inventory.source_stats.helpers,
        create_effects: inventory.source_stats.create_effects,
        returned_keys: inventory.source_stats.returned_keys,
      },
      return_surface: inventory.return_surface,
      signals: inventory.signals,
      memos: inventory.memos,
      effects: inventory.effects,
      event_handlers: inventory.event_handlers,
    },
    ...inventory.extracted_controllers,
  ];
  lines.push("# OpenCrew G1d Controller Dependency Report");
  lines.push("");
  lines.push("- Generated by: `frontend/scripts/g1d_controller_contract.mjs --write`");
  lines.push(`- Source: \`${inventory.controller}\``);
  lines.push(`- Controller lines: ${inventory.source_stats.controller_lines}`);
  lines.push(`- Signals: ${inventory.source_stats.signals}`);
  lines.push(`- Memos: ${inventory.source_stats.memos}`);
  lines.push(`- Named helpers: ${inventory.source_stats.helpers}`);
  lines.push(`- Returned keys: ${inventory.source_stats.returned_keys}`);
  lines.push(`- Extracted controllers: ${inventory.source_stats.extracted_controllers}`);
  lines.push(`- Extracted controller lines: ${inventory.source_stats.extracted_controller_lines}`);
  lines.push(`- Extracted controller signals: ${inventory.source_stats.extracted_controller_signals}`);
  lines.push(`- Extracted controller memos: ${inventory.source_stats.extracted_controller_memos}`);
  lines.push(`- Extracted controller helpers: ${inventory.source_stats.extracted_controller_helpers}`);
  lines.push(`- Extracted controller returned keys: ${inventory.source_stats.extracted_controller_returned_keys}`);
  lines.push("");
  lines.push("## Current Boundary State");
  lines.push("");
  lines.push("- `useShellRoutingController` owns `activeNav`, `routeHash`, hashchange routing, retired-route handling, role guards, and routing-to-metering callback dispatch.");
  lines.push("- `useOpenCrewAppController` still owns auth/bootstrap and wires routing callbacks into metering/session controllers.");
  lines.push("- Extracted controllers currently own shell layout state, sessions/events state, connection-test state, ASR settings, media settings, Mihomo settings, metering state/actions, and shell routing state/effects.");
  lines.push("- Extracted shell controllers do not import each other; `useOpenCrewAppController` wires dependencies and returned keys.");
  lines.push("");
  lines.push("## Sources");
  lines.push("");
  for (const controller of allControllers) {
    lines.push(`- \`${controller.source}\` -> \`${controller.name}\` (${controller.source_stats.controller_lines} lines, ${controller.source_stats.signals} signals, ${controller.source_stats.memos} memos, ${controller.source_stats.helpers} helpers, ${controller.source_stats.returned_keys} returned keys)`);
  }
  lines.push("");
  lines.push("## Return Surface");
  lines.push("");
  lines.push(`- Snapshot: \`${rel(SNAPSHOT_PATH)}\``);
  lines.push(`- Key count: ${inventory.return_surface.key_count}`);
  lines.push("- ShellView top-level consumed props:");
  lines.push(bulletList(inventory.shell_view_top_level_props));
  lines.push("");
  lines.push("## Extracted Controller Return Surfaces");
  lines.push("");
  for (const controller of inventory.extracted_controllers) {
    lines.push(`### ${controller.name}`);
    lines.push("");
    lines.push(`- Source: \`${controller.source}\``);
    lines.push(`- Key count: ${controller.return_surface.key_count}`);
    lines.push("- Keys:");
    lines.push(bulletList(controller.return_surface.keys));
    lines.push("");
  }
  lines.push("## Tracked Effects");
  lines.push("");
  for (const controller of allControllers) pushEffectReport(lines, controller);
  lines.push("## Event Handlers And Non-Reactive Reads");
  lines.push("");
  for (const controller of allControllers) pushEventHandlerReport(lines, controller);
  lines.push("## Signal Inventory");
  lines.push("");
  for (const controller of allControllers) pushSignalInventory(lines, controller);
  lines.push("## Memo Inventory");
  lines.push("");
  for (const controller of allControllers) pushMemoInventory(lines, controller);
  while (lines.at(-1) === "") lines.pop();
  return `${lines.join("\n")}\n`;
}

function writeReport(inventory) {
  writeFileSync(REPORT_PATH, renderReport(inventory));
}

function verifySnapshot(snapshot) {
  if (!existsSync(SNAPSHOT_PATH)) {
    throw new Error(`${rel(SNAPSHOT_PATH)} is missing; run npm --prefix frontend run inventory:g1d-controller`);
  }
  const expected = JSON.parse(readSource(SNAPSHOT_PATH));
  assert.deepEqual(snapshot, expected, `${rel(SNAPSHOT_PATH)} is stale; run npm --prefix frontend run inventory:g1d-controller`);
}

function verifyReport(inventory) {
  if (!existsSync(REPORT_PATH)) {
    throw new Error(`${rel(REPORT_PATH)} is missing; run npm --prefix frontend run inventory:g1d-controller`);
  }
  const expected = renderReport(inventory);
  const actual = readSource(REPORT_PATH);
  assert.equal(actual, expected, `${rel(REPORT_PATH)} is stale; run npm --prefix frontend run inventory:g1d-controller`);
}

const args = new Set(process.argv.slice(2));
const inventory = buildInventory(readSource(CONTROLLER_PATH), readSource(SHELL_VIEW_PATH), listExtractedControllerSources());
const snapshot = snapshotFromInventory(inventory);

if (args.has("--write")) {
  writeJson(SNAPSHOT_PATH, snapshot);
  writeReport(inventory);
  console.log(`wrote ${rel(SNAPSHOT_PATH)}`);
  console.log(`wrote ${rel(REPORT_PATH)}`);
} else {
  verifySnapshot(snapshot);
  verifyReport(inventory);
  console.log(`G1d controller contract passed (${snapshot.key_count} root keys; ${inventory.extracted_controllers.length} extracted controller surfaces; dependency report fresh)`);
}
