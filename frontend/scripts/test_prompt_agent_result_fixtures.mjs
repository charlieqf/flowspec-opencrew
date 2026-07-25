import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  extractPromptAgentResult,
  normalizedResult,
  promptAgentResultDocIds,
} from "../src/modules/koubo/UploadAssetLibrary/promptAgent/promptAgentResult.js";


const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "../..");
const fixturePath = resolve(repoRoot, "ToolLibrary/PromptKnowledge/fixtures/prompt_agent_result/cases.json");
const cases = JSON.parse(readFileSync(fixturePath, "utf8"));

function assertExpectedParse(actual, expected, name) {
  if (expected === null) {
    assert.equal(actual, null, `${name}: expected no parsed result`);
    return;
  }
  assert.ok(actual, `${name}: expected parsed result`);
  for (const [key, value] of Object.entries(expected)) {
    if (key === "used_source_doc_ids") {
      assert.deepEqual(promptAgentResultDocIds(actual), value, `${name}: used_source_doc_ids`);
    } else {
      assert.deepEqual(actual[key], value, `${name}: ${key}`);
    }
  }
}

for (const item of cases) {
  const actual = normalizedResult(extractPromptAgentResult(item.input));
  assertExpectedParse(actual, item.expected_parse, item.name);
}

console.log(`prompt_agent_result fixtures passed (${cases.length} cases)`);
