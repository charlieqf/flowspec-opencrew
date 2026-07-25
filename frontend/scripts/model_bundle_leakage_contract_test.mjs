import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const CONTRACT_PATH = resolve(SCRIPT_DIR, "model_bundle_leakage_contract.mjs");

function runFixture(name, files, expectedSuccess) {
  const root = mkdtempSync(join(tmpdir(), `opencrew-bundle-policy-${name}-`));
  try {
    for (const [filename, content] of Object.entries(files)) {
      writeFileSync(join(root, filename), content, "utf8");
    }
    const result = spawnSync(process.execPath, [CONTRACT_PATH, root], {
      encoding: "utf8",
    });
    const succeeded = result.status === 0;
    if (succeeded !== expectedSuccess) {
      throw new Error([
        `Fixture ${name} ${expectedSuccess ? "failed unexpectedly" : "passed unexpectedly"}.`,
        result.stdout,
        result.stderr,
      ].filter(Boolean).join("\n"));
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

runFixture("safe", { "index.js": "const publicPresets = ['Max', 'Flash']; const sync_mode = 'preserve';" }, true);
runFixture("model-family", { "index.js": "const model = 'sora-2';" }, false);
runFixture("newer-model-family", { "index.js": "const model = 'deepseek-v4-flash-free';" }, false);
runFixture("provider-brand", { "index.js": "const provider = 'heygen';" }, false);
runFixture("forbidden-field", { "index.js": "const provider_label_real = 'hidden';" }, false);
runFixture("debt-budget-overflow", { "index.js": Array(24).fill("'qwen'").join(",") }, false);
runFixture("source-map", { "index.js": "const publicPreset = 'Max';", "index.js.map": "{}" }, false);

console.log("Model bundle leakage contract self-test passed: deny patterns, debt budgets, and source-map rejection all fail closed.");
