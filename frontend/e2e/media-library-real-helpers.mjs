import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const helperDir = dirname(fileURLToPath(import.meta.url));

export const repoRoot = resolve(helperDir, "../..");

export function baseUrl() {
  return (
    process.env.OPENCREW_FRONTEND_URL
    || "http://127.0.0.1:18080"
  ).replace(/\/$/, "");
}

export function requiredEnv(name) {
  const value = String(process.env[name] || "").trim();
  assert.ok(value, `${name} is required for this real E2E`);
  return value;
}

export function readEnvFile(path) {
  if (!existsSync(path)) return {};
  const values = {};
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    const match = line.match(
      /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$/,
    );
    if (!match) continue;
    let value = match[2].trim();
    if (
      (value.startsWith("\"") && value.endsWith("\""))
      || (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    values[match[1]] = value;
  }
  return values;
}

export async function loginAdmin(context, url = baseUrl()) {
  const envFile = readEnvFile(
    resolve(repoRoot, ".opencrew-e2e-auth.env"),
  );
  const password = (
    process.env.OPENCREW_E2E_ADMIN_PASSWORD
    || envFile.OPENCREW_E2E_ADMIN_PASSWORD
    || ""
  );
  assert.ok(
    password,
    "OPENCREW_E2E_ADMIN_PASSWORD is required in the environment or .opencrew-e2e-auth.env",
  );
  const response = await context.request.post(
    `${url}/api/auth/login`,
    { data: { password }, timeout: 15_000 },
  );
  assert.equal(
    response.status(),
    200,
    `admin login failed: HTTP ${response.status()}`,
  );
}

export async function assertMediaLibraryCapabilities(
  context,
  url = baseUrl(),
) {
  const response = await context.request.get(
    `${url}/api/media-library/capabilities`,
    { timeout: 15_000 },
  );
  assert.equal(response.status(), 200);
  const payload = await responseJson(
    response,
    "media-library capabilities",
  );
  assert.equal(
    payload.schema_version,
    "media_library_capabilities_v1",
  );
  for (const key of [
    "analysis_runs",
    "library_search",
    "visual_semantic",
    "composite",
    "editor",
    "visual_search_v1",
    "clip_search_v1",
  ]) {
    assert.equal(
      typeof payload.features?.[key]?.enabled,
      "boolean",
      `capability ${key}.enabled must be boolean`,
    );
    assert.equal(
      typeof payload.features?.[key]?.configuration_valid,
      "boolean",
      `capability ${key}.configuration_valid must be boolean`,
    );
  }
  return payload;
}

export async function responseJson(response, label) {
  const body = await response.text();
  let payload = {};
  try {
    payload = body ? JSON.parse(body) : {};
  } catch {
    assert.fail(
      `${label} returned non-JSON HTTP ${response.status()}: ${body.slice(0, 500)}`,
    );
  }
  return payload;
}

export async function poll(
  operation,
  predicate,
  {
    timeoutMs = 90_000,
    intervalMs = 750,
    label = "condition",
  } = {},
) {
  const deadline = Date.now() + timeoutMs;
  let lastValue;
  let lastError;
  while (Date.now() < deadline) {
    try {
      lastValue = await operation();
      lastError = undefined;
      if (predicate(lastValue)) return lastValue;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolvePromise) => {
      setTimeout(resolvePromise, intervalMs);
    });
  }
  if (lastError) throw lastError;
  assert.fail(
    `timed out waiting for ${label}; last value: ${JSON.stringify(lastValue)}`,
  );
}

export function storyboardDialogues(payload) {
  const result = [];
  for (const shot of payload?.plan?.shots || []) {
    for (const scene of shot?.scenes || []) {
      for (const dialogue of scene?.dialogues || []) {
        if (dialogue && typeof dialogue === "object") {
          result.push(dialogue);
        }
      }
    }
  }
  return result;
}

export async function findStableStoryboardDialogue(
  context,
  url = baseUrl(),
) {
  const requestedTaskId = Number(
    process.env.MEDIA_LIBRARY_STORYBOARD_E2E_TASK_ID || 0,
  );
  const requestedDialogueAssetKey = String(
    process.env.MEDIA_LIBRARY_STORYBOARD_E2E_DIALOGUE_ASSET_KEY || "",
  ).trim();
  const targetsResponse = await context.request.get(
    `${url}/api/media-library/import-targets/storyboards`,
    { timeout: 15_000 },
  );
  assert.equal(
    targetsResponse.status(),
    200,
    "failed to load real StoryBoard import targets",
  );
  const targets = (await responseJson(
    targetsResponse,
    "StoryBoard targets",
  )).items || [];
  const eligibleTargets = targets
    .filter((target) => (
      !requestedTaskId
      || Number(target?.task_id) === requestedTaskId
    ))
    .slice(0, 30);
  if (requestedTaskId) {
    assert.ok(
      eligibleTargets.length > 0,
      `requested real StoryBoard Task ${requestedTaskId} is not an import target`,
    );
  }
  for (const target of eligibleTargets) {
    const taskId = Number(target?.task_id);
    if (!Number.isSafeInteger(taskId) || taskId <= 0) continue;
    const endpoint = `${url}/api/koubo-storyboard/tasks/${taskId}`;
    let firstResponse;
    let secondResponse;
    try {
      firstResponse = await context.request.get(endpoint, {
        timeout: 15_000,
      });
      secondResponse = await context.request.get(endpoint, {
        timeout: 15_000,
      });
    } catch {
      continue;
    }
    if (
      firstResponse.status() !== 200
      || secondResponse.status() !== 200
    ) {
      continue;
    }
    const firstPayload = await responseJson(
      firstResponse,
      `StoryBoard task ${taskId}`,
    );
    const secondPayload = await responseJson(
      secondResponse,
      `StoryBoard task ${taskId} repeated`,
    );
    const repeatedKeys = new Set(
      storyboardDialogues(secondPayload)
        .map((item) => String(item.dialogue_asset_key || "").trim())
        .filter(Boolean),
    );
    for (const dialogue of storyboardDialogues(firstPayload)) {
      const dialogueAssetKey = String(
        dialogue?.dialogue_asset_key || "",
      ).trim();
      if (
        /^[A-Za-z0-9._:-]{1,255}$/.test(dialogueAssetKey)
        && repeatedKeys.has(dialogueAssetKey)
        && (
          !requestedDialogueAssetKey
          || dialogueAssetKey === requestedDialogueAssetKey
        )
      ) {
        return {
          taskId,
          dialogueAssetKey,
          dialogueText: String(dialogue?.text || "").trim(),
        };
      }
    }
  }
  throw new Error(
    requestedTaskId || requestedDialogueAssetKey
      ? "The requested real StoryBoard task/dialogue does not expose a safe dialogue_asset_key that remains stable across repeated task-detail reads."
      : "No real StoryBoard target exposes a safe dialogue_asset_key that remains stable across repeated task-detail reads.",
  );
}
