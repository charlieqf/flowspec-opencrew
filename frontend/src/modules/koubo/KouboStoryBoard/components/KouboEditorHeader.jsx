import { For, Show } from "solid-js";
import { HostProductIcon, MessageIcon, RefreshIcon, SaveIcon, ShuffleIcon } from "../kouboStoryboardIcons.jsx";
import KouboTimingMenu from "./KouboTimingMenu.jsx";

export default function KouboEditorHeader(props) {
  const refreshVariablesTitle = () => {
    if (props.busy() === "refresh-session-vars") return "正在运行 00 并读取 Session Variables";
    if (props.dirty()) return "请先保存当前 StoryBoard，再运行 00";
    return "运行 00 并查看 Session Variables";
  };

  return <header class="kbsp-workspace-head">
    <div>
      <p class="kbsp-source-meta">
        <span>Task #{props.task()?.id}</span>
        <span>Session #{props.task()?.session_id}</span>
      </p>
    </div>
    <div class="kbsp-head-actions">
      <button
        class="kbsp-toolbar-btn icon-only kbsp-refresh-vars-entry"
        type="button"
        title={refreshVariablesTitle()}
        aria-label={refreshVariablesTitle()}
        disabled={!props.task() || props.dirty() || Boolean(props.busy())}
        onClick={() => void props.refreshSessionVariables?.()}
      >
        <RefreshIcon />
      </button>
      <button class="kbsp-toolbar-btn icon-only" type="button" title="故事版 Agent" aria-label="故事版 Agent" disabled={!props.task()} onClick={() => props.openStoryboardAgent?.()}><MessageIcon /></button>
      <button class="kbsp-toolbar-btn icon-only kbsp-host-product-entry" type="button" title="Host & Product Builder" aria-label="Host & Product Builder" disabled={!props.task()} onClick={() => props.setHostProductBuilderOpen(true)}><HostProductIcon /></button>
      <div class="kbsp-menu-wrap">
        <button class="kbsp-toolbar-btn icon-only" type="button" title="固定分组" onClick={() => {
          props.setTimingMenuOpen(false);
          props.setFixedMenuOpen(!props.fixedMenuOpen());
        }}><ShuffleIcon /></button>
        <Show when={props.fixedMenuOpen()}>
          <div class="kbsp-menu-panel">
            <label>分镜时长</label>
            <div class="kbsp-choice-grid">
              <For each={[8, 16]}>{(seconds) => <button class={props.fixedShotSeconds() === seconds ? "is-active" : ""} type="button" onClick={() => props.setFixedShotSeconds(seconds)}>{seconds}s</button>}</For>
            </div>
            <label>场景时长</label>
            <div class="kbsp-choice-grid sky">
              <For each={[4, 8]}>{(seconds) => <button class={props.fixedSceneSeconds() === seconds ? "is-active" : ""} type="button" onClick={() => props.setFixedSceneSeconds(seconds)}>{seconds}s</button>}</For>
            </div>
            <button class="kbsp-apply-btn" type="button" onClick={props.reorganizeFixedStoryboard}>应用固定时长</button>
          </div>
        </Show>
      </div>
      <KouboTimingMenu
        timingMenuOpen={props.timingMenuOpen}
        setTimingMenuOpen={(value) => {
          props.setFixedMenuOpen(false);
          props.setTimingMenuOpen(value);
        }}
        timingModel={props.timingModel}
        buildGSecondsPerChar={props.buildGSecondsPerChar}
        refreshDialogueTimingsOnly={props.refreshDialogueTimingsOnly}
        openAudioSettings={props.openAudioSettings}
        audioSettings={props.audioSettings}
        saveAudioSettings={props.saveAudioSettings}
        ttsProviderOptions={props.ttsProviderOptions}
        ttsModelsForProvider={props.ttsModelsForProvider}
        ttsVoicesForModel={props.ttsVoicesForModel}
        roleAccess={props.roleAccess}
      />
      <button class="kbsp-toolbar-btn icon-only" type="button" title={props.busy() === "save" ? "Saving" : "Save"} disabled={!props.dirty() || props.busy() === "save"} onClick={() => void props.savePlan()}><SaveIcon /></button>
    </div>
  </header>;
}
