export function text(value, fallback = "") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

export function extractPromptAgentResult(source) {
  const pattern = /<PROMPT_AGENT_RESULT>([\s\S]*?)<\/PROMPT_AGENT_RESULT>/g;
  let match;
  let latest = null;
  while ((match = pattern.exec(source || ""))) {
    try {
      const parsed = JSON.parse(match[1].trim());
      if (parsed && typeof parsed === "object") latest = parsed;
    } catch {
      // Ignore incomplete or malformed streaming blocks.
    }
  }
  return latest;
}

export function visibleText(source) {
  let value = String(source || "").replace(/<PROMPT_AGENT_RESULT>[\s\S]*?<\/PROMPT_AGENT_RESULT>/g, "").trim();
  const openIndex = value.indexOf("<PROMPT_AGENT_RESULT>");
  if (openIndex >= 0) value = value.slice(0, openIndex).trim();
  return value;
}

export function normalizedResult(result) {
  if (!result || typeof result !== "object") return null;
  const mode = ["critique", "optimize", "rewrite", "adapt"].includes(result.mode) ? result.mode : "optimize";
  const issues = Array.isArray(result.issues) ? result.issues : [];
  const modelNotes = Array.isArray(result.model_notes) ? result.model_notes : [];
  const changes = Array.isArray(result.changes) ? result.changes : [];
  const usedSources = Array.isArray(result.used_sources) ? result.used_sources : [];
  return {
    mode,
    summary: text(result.summary),
    issues,
    revised_prompt: text(result.revised_prompt || result.prompt),
    negative_prompt: text(result.negative_prompt),
    changes,
    model_notes: modelNotes,
    used_sources: usedSources,
    retrieval_id: text(result.retrieval_id),
  };
}

export function promptAgentResultDocIds(result) {
  if (!result || typeof result !== "object") return [];
  return (Array.isArray(result.used_sources) ? result.used_sources : [])
    .map((source) => text(source?.doc_id).trim())
    .filter(Boolean);
}
