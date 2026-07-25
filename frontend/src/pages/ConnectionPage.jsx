import { For, Show } from "solid-js";
import { api } from "../lib/api";
import {
  CheckIcon,
  CodeIcon,
  DiscoverIcon,
  InstallIcon,
  LoginIcon,
  SaveIcon,
  StatusBadge,
  TrashIcon,
  probeLabel,
} from "../shell/appShellUtils.jsx";

export default function ConnectionPage(props) {
  const {
    state,
    busyStep,
    runAction,
    runDiscover,
    opencodeBaseUrl,
    setOpencodeBaseUrl,
    setOpencodeDirty,
    opencodeUsername,
    setOpencodeUsername,
    opencodePassword,
    setOpencodePassword,
    opencodeCandidates,
    saveOpenCodeConfig,
    loginToOpenCode,
    discoverAttempted,
    npcStepStatus,
    startNpcTask,
    openEditor,
    openRunDialog,
    npcState,
    runDialog,
    npcResultVariant,
    npcMessage,
    npcResultMessage,
    publishStepStatus,
    currentPublishUrl,
    savePublishConfig,
    startPublishValidationTask,
    npcVerified,
    publishUrl,
    setPublishUrl,
    setPublishDirty,
    publishPreview,
    publishHasIssue,
    publishMessage,
    publishLastError,
    mihomoConfig,
    mihomoBusy,
    testMihomoConfig,
    saveMihomoConfig,
    mihomoSubscriptionUrl,
    setMihomoSubscriptionUrl,
    mihomoTestResult,
    mihomoError
  } = props;
  return (
          <>
            <div class="step-grid">
              <section class="card step-panel">
                <div class="step-card-head">
                  <div class="step-title-wrap">
                    <span class="step-badge">1</span>
                    <h2>OpenCode</h2>
                    <StatusBadge status={state()?.opencode?.status}/>
                  </div>
                  <div class="step-actions">
                    <button class="icon-action" disabled={busyStep() === "opencodeDiscover"} onClick={() => runAction("opencodeDiscover", runDiscover)} title="Discover Server"><DiscoverIcon /></button>
                    <button class="icon-action" disabled={busyStep() === "opencodeCheck" || !opencodeBaseUrl().trim()} onClick={() => runAction("opencodeCheck", () => api.opencodeCheck(opencodeBaseUrl(), opencodeUsername(), opencodePassword()))} title="Check Server"><CheckIcon /></button>
                    <button class="icon-action" disabled={busyStep() === "opencodeSave" || !opencodeBaseUrl().trim()} onClick={() => runAction("opencodeSave", () => saveOpenCodeConfig())} title="Save URL"><SaveIcon /></button>
                  </div>
                </div>
                <div class="step-card-body">
                  <div class="field-row step-inputs step-input-shell">
                    <div class="step-input-segment step-input-segment-wide">
                      <input placeholder="OpenCode server URL" value={opencodeBaseUrl()} onInput={(e) => {
            setOpencodeBaseUrl(e.currentTarget.value);
            setOpencodeDirty(true);
        }}/>
                    </div>
                    <div class="step-input-segment">
                      <input placeholder="Username" value={opencodeUsername()} onInput={(e) => {
            setOpencodeUsername(e.currentTarget.value);
            setOpencodeDirty(true);
        }}/>
                    </div>
                    <div class="step-input-segment">
                      <input placeholder="Password" type="password" value={opencodePassword()} onInput={(e) => {
            setOpencodePassword(e.currentTarget.value);
            setOpencodeDirty(true);
        }}/>
                    </div>
                  </div>
                  <Show when={opencodeCandidates().length > 0}>
                    <div class="candidate-list">
                      <For each={opencodeCandidates()}>{(candidate) => (<div class={`candidate ${candidate.healthy ? "healthy" : ""}`}>
                          <button class="candidate-body" onClick={() => void runAction("opencodeSave", () => saveOpenCodeConfig(candidate.base_url, candidate.username ?? "", candidate.password ?? ""))}>
                            <div class="candidate-main">{candidate.base_url}</div>
                            <div class="candidate-sub">{candidate.process} {candidate.pid ? `(pid ${candidate.pid})` : ""} - {probeLabel(candidate)}</div>
                          </button>
                          <button class="candidate-login" disabled={!candidate.username || !candidate.password} onClick={() => loginToOpenCode(candidate.base_url, candidate.username ?? "", candidate.password ?? "")} title="Login with this server"><LoginIcon /></button>
                        </div>)}</For>
                    </div>
                  </Show>
                  <Show when={discoverAttempted() && opencodeCandidates().length === 0}>
                    <div class="message-panel">
                      <p class="helper">No running OpenCode server process with a listening port was found on this machine.</p>
                    </div>
                  </Show>
                </div>
              </section>

              <section class="card step-panel">
                <div class="step-card-head npc-step-head">
                  <div class="step-title-wrap">
                    <span class="step-badge">2</span>
                    <h2>NPC</h2>
                    <StatusBadge status={npcStepStatus()}/>
                  </div>
                  <div class="step-actions">
                    <button class="icon-action" disabled={busyStep() === "npcInstall"} onClick={() => runAction("npcInstall", () => startNpcTask("install", api.npcInstall))} aria-label="Install NPC" title="Install NPC">
                      <InstallIcon />
                    </button>
                    <button class="icon-action" onClick={() => void openEditor("install")} aria-label="Edit install skill" title="Edit install skill">
                      <CodeIcon />
                    </button>
                    <button class="icon-action" disabled={busyStep() === "npcReconnect"} onClick={() => void openRunDialog()} aria-label="Reconnect NPC" title="Reconnect NPC">
                      <CheckIcon />
                    </button>
                    <button class="icon-action" onClick={() => void openEditor("run")} aria-label="Edit reconnect skill" title="Edit reconnect skill">
                      <CodeIcon />
                    </button>
                    <button class="icon-action danger" disabled={busyStep() === "npcUninstall"} onClick={() => runAction("npcUninstall", () => startNpcTask("uninstall", api.npcUninstall))} aria-label="Uninstall NPC" title="Uninstall NPC">
                      <TrashIcon />
                    </button>
                    <button class="icon-action" onClick={() => void openEditor("uninstall")} aria-label="Edit uninstall skill" title="Edit uninstall skill">
                      <CodeIcon />
                    </button>
                  </div>
                </div>
                <div class="step-card-body">
                  <div class="param-table-container npc-step-meta">
                    <div class="param-row">
                      <div class="param-label">Binary Path</div>
                      <div class="param-value"><span class="inline-code">{String(npcState().command_path ?? "-")}</span></div>
                    </div>
                    <div class="param-row">
                      <div class="param-label">Configuration</div>
                      <div class="param-value"><span class="inline-code">{String(runDialog().conf_path ?? "~/.opencrew/npc/conf/npc.conf")}</span></div>
                    </div>
                  </div>
                  <div class={`param-table-container message-table ${npcResultVariant()}`}>
                    <div class="param-row">
                      <div class="param-label">Message</div>
                      <div class="param-value">{npcMessage()}</div>
                    </div>
                    <div class="param-row">
                      <div class="param-label">Last Result</div>
                      <div class="param-value">{npcResultMessage()}</div>
                    </div>
                  </div>
                </div>
              </section>
            </div>

            <div class="step-grid">
              <section class="card step-panel">
                <div class="step-card-head">
                  <div class="step-title-wrap">
                    <span class="step-badge">3</span>
                    <h2>URL</h2>
                    <StatusBadge status={publishStepStatus()}/>
                  </div>
                  <div class="step-actions">
                    <button class="icon-action" disabled={busyStep() === "publishSave" || !currentPublishUrl()} onClick={() => runAction("publishSave", savePublishConfig)} title="Save URL"><SaveIcon /></button>
                    <button class="icon-action" onClick={() => void openEditor("publish_validate")} title="Edit URL validation skill"><CodeIcon /></button>
                    <button class="icon-action" disabled={busyStep() === "publishValidate" || !currentPublishUrl()} onClick={() => runAction("publishValidate", startPublishValidationTask)} title="Validate URL"><CheckIcon /></button>
                  </div>
                </div>
                <div class="step-card-body">
                  <Show when={npcVerified()} fallback={<div class="message-panel"><p class="helper">Finish NPC run before generating Nginx and NPS recommendations.</p></div>}>
                    <div class="field-row step-inputs step-input-shell step-input-shell-single">
                      <div class="step-input-segment step-input-segment-wide">
                        <input placeholder="Public URL, for example https://demo.example.com/opencode" value={publishUrl()} onInput={(e) => {
            setPublishUrl(e.currentTarget.value);
            setPublishDirty(true);
        }}/>
                      </div>
                    </div>
                    <div class="param-table-container">
                      <div class="param-row">
                        <div class="param-label">Normalized URL</div>
                        <div class="param-value"><span class="inline-code">{publishPreview()?.normalized_url || "-"}</span></div>
                      </div>
                      <div class="param-row">
                        <div class="param-label">Mode</div>
                        <div class="param-value">{publishPreview()?.deployment_mode || "-"}</div>
                      </div>
                      <div class="param-row">
                        <div class="param-label">Domain</div>
                        <div class="param-value">{publishPreview()?.domain || "-"}</div>
                      </div>
                      <div class="param-row">
                        <div class="param-label">Path Prefix</div>
                        <div class="param-value"><span class="inline-code">{publishPreview()?.path_prefix || "-"}</span></div>
                      </div>
                      <div class="param-row">
                        <div class="param-label">Last Validation</div>
                        <div class="param-value tabular-data">{publishPreview()?.tested_at ? new Date(publishPreview().tested_at).toLocaleString() : "-"}</div>
                      </div>
                    </div>
                  </Show>
                  <div class={`param-table-container message-table ${publishHasIssue() ? "error-variant" : ""}`}>
                    <div class="param-row">
                      <div class="param-label">Message</div>
                      <div class="param-value">{publishMessage()}</div>
                    </div>
                    <div class="param-row">
                      <div class="param-label">Last Result</div>
                      <div class="param-value">{publishLastError()}</div>
                    </div>
                  </div>
                </div>
              </section>
            </div>

            <div class="step-grid">
              <section class="card step-panel">
                <div class="step-card-head">
                  <div class="step-title-wrap">
                    <span class="step-badge">4</span>
                    <h2>mihomo</h2>
                    <StatusBadge status={mihomoConfig()?.enabled ? mihomoConfig()?.running ? "connected" : "configured" : "idle"}/>
                  </div>
                  <div class="step-actions">
                    <button class="icon-action" disabled={mihomoBusy()} onClick={() => void testMihomoConfig()} title="Test mihomo"><CheckIcon /></button>
                    <button class="icon-action" disabled={mihomoBusy()} onClick={() => void saveMihomoConfig(!(mihomoConfig()?.enabled ?? false))} title={mihomoConfig()?.enabled ? "Disable mihomo" : "Enable mihomo"}><SaveIcon /></button>
                  </div>
                </div>
                <div class="step-card-body">
                  <div class="field-row step-inputs step-input-shell step-input-shell-single">
                    <div class="step-input-segment step-input-segment-wide">
                      <input type="password" placeholder={mihomoConfig()?.has_subscription_url ? "Leave blank to keep existing subscription URL" : "Paste subscription URL"} value={mihomoSubscriptionUrl()} onInput={(event) => setMihomoSubscriptionUrl(event.currentTarget.value)}/>
                    </div>
                  </div>
                  <div class="param-table-container">
                    <div class="param-row">
                      <div class="param-label">Status</div>
                      <div class="param-value">{mihomoConfig()?.enabled ? "Enabled" : "Disabled"} · {mihomoConfig()?.running ? "localhost proxy reachable" : "proxy not listening"}</div>
                    </div>
                    <div class="param-row">
                      <div class="param-label">Proxy</div>
                      <div class="param-value"><span class="inline-code">{mihomoConfig()?.proxy_url || "http://127.0.0.1:7890"}</span></div>
                    </div>
                    <div class="param-row">
                      <div class="param-label">Subscription</div>
                      <div class="param-value">{mihomoConfig()?.has_subscription_url ? "Saved in local secret store" : "Missing"}</div>
                    </div>
                    <Show when={mihomoTestResult()}>
                      <div class="param-row">
                        <div class="param-label">Last Test</div>
                        <div class="param-value">{mihomoTestResult()?.message}</div>
                      </div>
                    </Show>
                  </div>
                  <Show when={mihomoError()}>
                    <div class="banner bad">{mihomoError()}</div>
                  </Show>
                  <div class="asr-config-actions media-config-actions">
                    <button class="secondary" type="button" disabled={mihomoBusy()} onClick={() => void saveMihomoConfig(mihomoConfig()?.enabled ?? false)}>Save Subscription</button>
                  </div>
                </div>
              </section>
            </div>
          </>
  );
}
