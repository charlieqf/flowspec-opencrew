import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import FloatingAssetMenu from "../components/FloatingAssetMenu.jsx";
import FlowIcon from "../components/FlowIcon.jsx";
import "./promptAgent.css";

function versionTitle(item) {
  const prompt = String(item?.revised_prompt || item?.negative_prompt || item?.original_prompt || "").trim();
  if (!prompt) return String(item?.version_id || "Saved version");
  return prompt.length > 90 ? `${prompt.slice(0, 90)}...` : prompt;
}

function optimizedPromptText(item) {
  return String(item?.revised_prompt || item?.negative_prompt || "");
}

function formatTime(value) {
  const ts = Number(value || 0);
  if (!Number.isFinite(ts) || ts <= 0) return "";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return "";
  }
}

function diffTokens(value) {
  return String(value || "").match(/\s+|[A-Za-z0-9_'-]+|[\u4e00-\u9fff]|./gu) || [];
}

function pushDiffPart(parts, text, type = "same") {
  if (!text) return;
  const last = parts[parts.length - 1];
  if (last?.type === type) {
    last.text += text;
    return;
  }
  parts.push({ text, type });
}

function simpleTokenDiff(original, revised) {
  const left = [];
  const right = [];
  const a = diffTokens(original);
  const b = diffTokens(revised);
  if (!a.length && !b.length) return { left, right };
  if (a.length * b.length > 360000) {
    pushDiffPart(left, String(original || ""), "removed");
    pushDiffPart(right, String(revised || ""), "added");
    return { left, right };
  }
  const width = b.length + 1;
  const table = Array.from({ length: a.length + 1 }, () => new Uint16Array(width));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      pushDiffPart(left, a[i], "same");
      pushDiffPart(right, b[j], "same");
      i += 1;
      j += 1;
    } else if (j >= b.length || (i < a.length && table[i + 1][j] >= table[i][j + 1])) {
      pushDiffPart(left, a[i], "removed");
      i += 1;
    } else {
      pushDiffPart(right, b[j], "added");
      j += 1;
    }
  }
  return { left, right };
}

function PromptDiffCompare(props) {
  const diff = createMemo(() => simpleTokenDiff(props.original, props.revised));
  return <div class="prompt-agent-diff-compare">
    <div class="prompt-agent-diff-pane is-original">
      <h4>原始</h4>
      <pre><For each={diff().left}>{(part) => <span class={`prompt-agent-diff-part is-${part.type}`}>{part.text}</span>}</For></pre>
    </div>
    <div class="prompt-agent-diff-pane is-revised">
      <h4>优化后</h4>
      <pre><For each={diff().right}>{(part) => <span class={`prompt-agent-diff-part is-${part.type}`}>{part.text}</span>}</For></pre>
    </div>
  </div>;
}

function PromptVersionCard(props) {
  let menuButtonEl;
  const [menuOpen, setMenuOpen] = createSignal(false);
  const item = () => props.item || {};
  const versionId = () => String(item()?.version_id || "");
  const expanded = () => props.expandedId?.() === versionId();
  const stop = (event) => event.stopPropagation();
  const toggleDiff = (event) => {
    stop(event);
    setMenuOpen(false);
    props.onToggleDiff?.(versionId());
  };
  const deleteVersion = (event) => {
    stop(event);
    setMenuOpen(false);
    void props.onDelete?.(item());
  };

  return <article class={`prompt-agent-version-item ${expanded() ? "is-expanded" : ""} ${menuOpen() ? "is-menu-open" : ""}`}>
    <Show when={!expanded()}>
      <div class="prompt-agent-version-card-body">
        <header>
          <strong>{String(item()?.mode || "optimize")}</strong>
          <span>{formatTime(item()?.updated_at || item()?.created_at)}</span>
        </header>
        <p>{versionTitle(item())}</p>
        <Show when={item()?.negative_prompt}>
          <small>Negative prompt included</small>
        </Show>
      </div>
    </Show>
    <div class="ual-card-actions prompt-agent-version-actions">
      <button ref={(el) => { menuButtonEl = el; }} type="button" title="More" aria-expanded={menuOpen()} onClick={(event) => {
        stop(event);
        setMenuOpen((value) => !value);
      }}>
        <FlowIcon name="moreVert" />
      </button>
    </div>
    {menuOpen() ? <FloatingAssetMenu anchor={() => menuButtonEl} onClose={() => setMenuOpen(false)} onClick={stop}>
      <button type="button" role="menuitem" onClick={toggleDiff}><FlowIcon name="editSquare" />{expanded() ? "收起Diff" : "查看Diff"}</button>
      <hr />
      <button type="button" role="menuitem" class="is-danger" onClick={deleteVersion}><FlowIcon name="delete" />删除</button>
    </FloatingAssetMenu> : null}
    <Show when={expanded()}>
      <div class="prompt-agent-version-detail">
        <Show when={item()?.original_prompt || optimizedPromptText(item())}>
          <PromptDiffCompare original={item()?.original_prompt} revised={optimizedPromptText(item())} />
        </Show>
      </div>
    </Show>
  </article>;
}

export default function PromptAgentWorkspace(props) {
  const [versions, setVersions] = createSignal([]);
  const [error, setError] = createSignal("");
  const [expandedId, setExpandedId] = createSignal("");
  const toggleExpand = (id) => setExpandedId((current) => current === id ? "" : id);

  const taskId = () => Number(props.task?.()?.id || 0);
  const filteredVersions = createMemo(() => {
    const needle = String(props.query?.() || "").trim().toLowerCase();
    if (!needle) return versions();
    return versions().filter((item) => [
      item?.version_id,
      item?.mode,
      item?.summary,
      item?.original_prompt,
      item?.revised_prompt,
      item?.negative_prompt,
      ...(Array.isArray(item?.changes) ? item.changes : []),
      ...(Array.isArray(item?.model_notes) ? item.model_notes : []),
      ...(Array.isArray(item?.used_sources) ? item.used_sources.map((source) => `${source?.doc_id || ""} ${source?.title || ""}`) : []),
    ].some((value) => String(value || "").toLowerCase().includes(needle)));
  });

  const refresh = async () => {
    const id = taskId();
    if (!id) return;
    try {
      setError("");
      const payload = await props.api.promptAgentVersions(id);
      setVersions(Array.isArray(payload?.items) ? payload.items : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err || ""));
    }
  };

  const deleteVersion = async (item) => {
    const id = taskId();
    const versionId = String(item?.version_id || "");
    if (!id || !versionId) return;
    if (!window.confirm("删除这个 Prompt 版本？")) return;
    try {
      setError("");
      await props.api.deletePromptAgentVersion(id, versionId);
      setVersions((current) => current.filter((entry) => String(entry?.version_id || "") !== versionId));
      setExpandedId((current) => current === versionId ? "" : current);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err || ""));
    }
  };

  createEffect(() => {
    if (!taskId()) return;
    void refresh();
  });

  createEffect(() => {
    if (!props.refreshKey?.()) return;
    void refresh();
  });

  return <section class="prompt-agent-workspace">
    <header class="prompt-agent-workspace-header is-actions-only">
      <button type="button" title="Refresh" onClick={() => void refresh()}>
        <FlowIcon name="redo" />
      </button>
    </header>
    <Show when={error()}>
      <div class="prompt-agent-error">{error()}</div>
    </Show>
    <div class="prompt-agent-version-list">
      <Show when={filteredVersions().length} fallback={<div class="prompt-agent-empty">{versions().length ? "No matching versions" : "No saved versions"}</div>}>
        <For each={filteredVersions()}>{(item) => <PromptVersionCard item={item} expandedId={expandedId} onToggleDiff={toggleExpand} onDelete={deleteVersion} />}</For>
      </Show>
    </div>
  </section>;
}
