import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(SCRIPT_DIR, "..");
const REPO_ROOT = resolve(FRONTEND_ROOT, "..");
const DIST_ROOT = resolve(process.argv[2] || join(FRONTEND_ROOT, "dist"));
const POLICY_PATH = join(REPO_ROOT, "backend", "opcrew_backend", "model_leakage_policy.json");
const MEDIA_CATALOG_PATH = join(REPO_ROOT, "ModelConfig", "backend", "opcrew_model_config", "media_model_config.py");
const SCANNED_EXTENSIONS = new Set([".css", ".html", ".js", ".map"]);

function escapeRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function providerBrandPattern(name) {
  return `(?<![a-z0-9])${escapeRegex(name)}(?:[_-][a-z0-9]+)*(?=$|[^a-z0-9])`;
}

function compilePattern(id, label, source) {
  try {
    return { id, label, regex: new RegExp(source, "gi") };
  } catch (error) {
    throw new Error(`Invalid model leakage policy regex ${id}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function loadPolicy() {
  if (!existsSync(POLICY_PATH)) throw new Error(`Model leakage policy not found: ${POLICY_PATH}`);
  const policy = JSON.parse(readFileSync(POLICY_PATH, "utf8"));
  if (!policy || Number(policy.version || 0) < 1) throw new Error("Model leakage policy must be a versioned JSON object");
  if (!Array.isArray(policy.provider_brands) || !policy.provider_brands.length || !Array.isArray(policy.model_patterns) || !policy.model_patterns.length) {
    throw new Error("Model leakage policy must define provider and model deny patterns");
  }
  const patterns = [];
  for (const name of policy.provider_brands || []) {
    patterns.push(compilePattern(`provider-brand:${name}`, `provider brand ${name}`, providerBrandPattern(name)));
  }
  for (const [index, source] of (policy.provider_literal_patterns || []).entries()) {
    patterns.push(compilePattern(`provider-literal:${index}`, "provider literal", source));
  }
  for (const [index, source] of (policy.domain_patterns || []).entries()) {
    patterns.push(compilePattern(`domain:${index}`, "provider endpoint/domain", source));
  }
  for (const [index, source] of (policy.model_patterns || []).entries()) {
    patterns.push(compilePattern(`model-family:${index}`, "real model family", source));
  }
  for (const field of policy.forbidden_fields || []) {
    patterns.push(compilePattern(`forbidden-field:${field}`, `forbidden mapping field ${field}`, escapeRegex(field)));
  }
  for (const literal of policy.fixed_literals || []) {
    patterns.push(compilePattern(`fixed-literal:${literal}`, "fixed account/team locator", escapeRegex(literal)));
  }
  for (const [index, source] of (policy.pricing_patterns || []).entries()) {
    patterns.push(compilePattern(`pricing:${index}`, "embedded provider pricing", source));
  }
  if (!patterns.length) throw new Error("Model leakage policy produced no bundle patterns");
  return {
    patterns,
    allowances: policy.temporary_bundle_allowances || {},
  };
}

function allowanceFor(id) {
  const value = TEMPORARY_ALLOWANCES[id];
  if (value && typeof value === "object") return Number(value.max_matches || 0);
  return Number(value || 0);
}

const REQUIRED_POLICY_TEST_VECTORS = [
  "sora-2",
  "veo-3.1-generate-preview",
  "kling-3.0-turbo",
  "wan2.7-r2v",
  "gemini-2.5-flash",
  "deepseek-v4-flash-free",
  "heygen",
  "chanjing",
  "cosyvoice",
  "minimax",
  "provider_label_real",
  "model_label_real",
];

const { patterns: DENIED_PATTERNS, allowances: TEMPORARY_ALLOWANCES } = loadPolicy();
for (const sample of REQUIRED_POLICY_TEST_VECTORS) {
  if (!DENIED_PATTERNS.some(({ regex }) => {
    regex.lastIndex = 0;
    return regex.test(sample);
  })) {
    throw new Error(`Model leakage policy does not cover required test vector: ${sample}`);
  }
}

if (existsSync(MEDIA_CATALOG_PATH)) {
  const catalogSource = readFileSync(MEDIA_CATALOG_PATH, "utf8");
  const catalogProviders = new Set(Array.from(catalogSource.matchAll(/"provider"\s*:\s*"([^"]+)"/g), (match) => match[1]));
  for (const provider of catalogProviders) {
    const samples = [provider, `"${provider}"`];
    const covered = DENIED_PATTERNS.some(({ regex }) => samples.some((sample) => {
      regex.lastIndex = 0;
      return regex.test(sample);
    }));
    if (!covered) throw new Error(`Model leakage policy does not cover catalog provider: ${provider}`);
  }
}

function extension(path) {
  const index = path.lastIndexOf(".");
  return index >= 0 ? path.slice(index).toLowerCase() : "";
}

function filesUnder(root) {
  const files = [];
  for (const name of readdirSync(root)) {
    const path = join(root, name);
    if (statSync(path).isDirectory()) files.push(...filesUnder(path));
    else if (SCANNED_EXTENSIONS.has(extension(path))) files.push(path);
  }
  return files;
}

if (!existsSync(DIST_ROOT)) {
  console.error(`Frontend bundle leakage contract failed: dist directory not found: ${DIST_ROOT}`);
  process.exit(1);
}

const files = filesUnder(DIST_ROOT);
if (!files.length) {
  console.error(`Frontend bundle leakage contract failed: no built assets found under ${DIST_ROOT}`);
  process.exit(1);
}
const findings = [];
const countsByPattern = new Map();
for (const path of files) {
  const rel = relative(FRONTEND_ROOT, path);
  if (extension(path) === ".map") {
    findings.push({ file: rel, id: `source-map:${rel}`, label: "source map", count: 1, unconditional: true });
    continue;
  }
  const content = readFileSync(path, "utf8");
  for (const { id, label, regex } of DENIED_PATTERNS) {
    regex.lastIndex = 0;
    const count = Array.from(content.matchAll(regex)).length;
    if (count) {
      countsByPattern.set(id, (countsByPattern.get(id) || 0) + count);
      findings.push({ file: rel, id, label, count });
    }
  }
}

const excessiveFindings = findings.filter((finding) => {
  if (finding.unconditional) return true;
  const allowed = allowanceFor(finding.id);
  return (countsByPattern.get(finding.id) || 0) > allowed;
});
const unusedAllowances = Object.entries(TEMPORARY_ALLOWANCES).filter(([id, allowed]) => (
  (typeof allowed === "object" ? Number(allowed?.max_matches || 0) : Number(allowed || 0)) > 0
  && !DENIED_PATTERNS.some((pattern) => pattern.id === id)
));

if (unusedAllowances.length) {
  console.error("Frontend bundle leakage contract failed: policy contains unknown temporary allowances:");
  for (const [id] of unusedAllowances) console.error(`- ${id}`);
  process.exit(1);
}

if (excessiveFindings.length) {
  console.error("Frontend bundle leakage contract failed:");
  for (const finding of excessiveFindings) {
    const total = countsByPattern.get(finding.id) || finding.count;
    const allowed = allowanceFor(finding.id);
    console.error(`- ${finding.file}: ${finding.label} [${finding.id}] (${finding.count} file match${finding.count === 1 ? "" : "es"}; ${total} total; ${allowed} temporarily allowed)`);
  }
  process.exit(1);
}

const activeDebt = [...countsByPattern.entries()].filter(([id, count]) => count > 0 && allowanceFor(id) >= count);
if (activeDebt.length) {
  console.warn("Frontend bundle leakage contract passed with explicit temporary debt:");
  for (const [id, count] of activeDebt) {
    const metadata = TEMPORARY_ALLOWANCES[id];
    const suffix = metadata && typeof metadata === "object" ? `; owner=${metadata.owner_phase || "unassigned"}; expires=${metadata.expires || "unspecified"}` : "";
    console.warn(`- ${id}: ${count}/${allowanceFor(id)} matches${suffix}`);
  }
}
console.log(`Frontend bundle leakage contract passed: ${files.length} built assets scanned with ${DENIED_PATTERNS.length} policy patterns.`);
