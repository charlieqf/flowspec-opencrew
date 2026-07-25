import { For, Show } from "solid-js";
import { ModelConfigModals } from "../../../ModelConfig/frontend/src/ModelConfigModule";
import {
  DEFAULT_NPC_MULTI_ACCOUNT_PATH,
  DEFAULT_NPC_SERVER_ADDR,
  buildMultiAccountPreview,
  buildNpcConfPreview,
  summarizeNpcResult,
} from "./appShellUtils.jsx";

export default function ShellDialogs(props) {
  const {
    runDialog,
    setRunDialog,
    busyStep,
    saveNpcConfig,
    reconnectNpcService,
    editorKind,
    setEditorKind,
    skills,
    editorContent,
    setEditorContent,
    saveEditor,
    restoreSkill,
    publishGuideOpen,
    setPublishGuideOpen,
    tasks,
    publishPreview,
    taskLogs,
    publishCheckGroups,
    envDialog,
    setEnvDialog
  } = props;
  return (
    <>
      <Show when={runDialog().open}>
        <div class="drawer-backdrop" onClick={() => setRunDialog((prev) => ({ ...prev, open: false }))}/>
        <section class="verify-dialog">
          <div class="env-dialog-head">
            <div>
              <h3>Reconnect NPC</h3>
              <p>OpenCrew will stop every active npc connection to 113.125.202.171:8024, regenerate the conf files if needed, and reconnect using the current config.</p>
            </div>
            <button class="secondary" onClick={() => setRunDialog((prev) => ({ ...prev, open: false }))}>Close</button>
          </div>
          <div class="meta">
            <span>Server Addr: {DEFAULT_NPC_SERVER_ADDR}</span>
            <span>Conf Path: {String(runDialog().conf_path ?? "~/.opencrew/npc/conf/npc.conf")}</span>
            <span>Multi Account Path: {String(runDialog().multi_account_path ?? DEFAULT_NPC_MULTI_ACCOUNT_PATH)}</span>
          </div>
          <div class="npc-config-section">
            <span class="npc-config-label">npc.conf</span>
            <textarea class="npc-config-preview" value={runDialog().conf_text || buildNpcConfPreview(runDialog())} onInput={(e) => setRunDialog((prev) => ({ ...prev, conf_text: e.currentTarget.value }))}/>
          </div>
          <div class="npc-config-section">
            <span class="npc-config-label">multi_account.conf</span>
            <textarea class="npc-config-preview npc-config-preview-small" value={runDialog().multi_account_text || buildMultiAccountPreview(runDialog().multi_account_line)} onInput={(e) => setRunDialog((prev) => ({ ...prev, multi_account_text: e.currentTarget.value }))}/>
          </div>
          <div class="field-row">
            <button class="secondary" onClick={() => void saveNpcConfig()}>Save Config</button>
            <button disabled={busyStep() === "npcReconnect" || runDialog().loading} onClick={() => void reconnectNpcService()}>{runDialog().loading ? "Reconnecting..." : "Reconnect NPC"}</button>
          </div>
          <Show when={runDialog().error}>
            <div class="banner bad">{runDialog().error}</div>
          </Show>
          <Show when={runDialog().result}>
            <div class="banner good">{summarizeNpcResult(runDialog().result)}</div>
          </Show>
        </section>
      </Show>

      <Show when={editorKind()}>
        <div class="drawer-backdrop" onClick={() => setEditorKind(null)}/>
        <section class="skill-drawer">
          <div class="skill-drawer-head">
            <div>
              <h3>{skills()[editorKind()]?.title}</h3>
              <p>Edit the live skill that powers this step.</p>
            </div>
            <button class="secondary" onClick={() => setEditorKind(null)}>Close</button>
          </div>
          <textarea class="skill-editor" value={editorContent()} onInput={(e) => setEditorContent(e.currentTarget.value)}/>
          <div class="field-row">
            <button onClick={() => void saveEditor()}>Save Draft</button>
            <button class="secondary" onClick={() => void restoreSkill(editorKind())}>Restore Default</button>
          </div>
        </section>
      </Show>

      <Show when={publishGuideOpen()}>
        <div class="drawer-backdrop" onClick={() => setPublishGuideOpen(false)}/>
        <section class="skill-drawer">
          <div class="skill-drawer-head">
            <div>
              <h3>URL Validation Result</h3>
              <p>Recommended mapping, complete validation checks, and the generated Markdown guide for this target URL.</p>
            </div>
            <button class="secondary" onClick={() => setPublishGuideOpen(false)}>Close</button>
          </div>
          <div class="meta">
            <span>Task Status: {tasks().publish_validate?.status || "idle"}</span>
            <span>Task ID: {tasks().publish_validate?.id ?? "-"}</span>
          </div>
          <div class="meta">
            <span>Normalized URL: {publishPreview()?.normalized_url || "-"}</span>
            <span>Mode: {publishPreview()?.deployment_mode || "-"}</span>
            <span>Local Frontend: {publishPreview()?.local_frontend_url || "http://127.0.0.1:18080/"}</span>
            <span>Local Backend API: {publishPreview()?.local_backend_api_url || "http://127.0.0.1:8011/api/"}</span>
            <span>Public API Probe: {publishPreview()?.public_api_url || "-"}</span>
            <span>allowedHosts: {publishPreview()?.allowed_hosts_hint || "Keep the target hostname in OpenCrew/frontend/vite.config.ts allowedHosts."}</span>
          </div>
          <Show when={(taskLogs().publish_validate ?? []).length > 0}>
            <div class="publish-check-group">
              <span class="npc-config-label">Live Validation Log</span>
              <div class="meta">
                <For each={taskLogs().publish_validate}>{(entry) => <span>{entry.phase}: {entry.message}</span>}</For>
              </div>
            </div>
          </Show>
          <div class="npc-config-section">
            <span class="npc-config-label">Recommended Nginx Config</span>
            <textarea class="npc-config-preview" value={publishPreview()?.nginx_config || ""} readOnly/>
          </div>
          <div class="npc-config-section">
            <span class="npc-config-label">Recommended NPS / NPC Config</span>
            <textarea class="npc-config-preview npc-config-preview-small" value={publishPreview()?.nps_config || ""} readOnly/>
          </div>
          <Show when={publishCheckGroups().length > 0}>
            <div class="publish-check-groups">
              <For each={publishCheckGroups()}>{([group, checks]) => (<div class="publish-check-group">
                  <span class="npc-config-label">{group}</span>
                  <div class="meta">
                    <For each={checks}>{(check) => <span>{check.ok ? "PASS" : "FAIL"} {check.name}: {check.message}{check.recommended_fix ? ` | Fix: ${check.recommended_fix}` : ""}</span>}</For>
                  </div>
                </div>)}</For>
            </div>
          </Show>
          <div class="npc-config-section">
            <span class="npc-config-label">Configuration Guide (Markdown)</span>
            <textarea class="skill-editor" value={publishPreview()?.guide_markdown || ""} readOnly/>
          </div>
        </section>
      </Show>

      <ModelConfigModals />

      <Show when={envDialog().open}>
        <div class="drawer-backdrop" onClick={() => setEnvDialog((prev) => ({ ...prev, open: false }))}/>
        <section class="env-dialog">
          <div class="env-dialog-head">
            <div>
              <h3>Environment Check</h3>
              <p>Freshly re-read from the machine after clearing the prior values.</p>
            </div>
            <button class="secondary" onClick={() => setEnvDialog((prev) => ({ ...prev, open: false }))}>Close</button>
          </div>
          <div class="info-grid env-grid">
            <div class="info-item"><label>Platform</label><span>{envDialog().platform || "-"}</span></div>
            <div class="info-item"><label>Architecture</label><span>{envDialog().arch || "-"}</span></div>
            <div class="info-item"><label>Environment</label><span>{envDialog().environment || "-"}</span></div>
          </div>
        </section>
      </Show>
    </>
  );
}
