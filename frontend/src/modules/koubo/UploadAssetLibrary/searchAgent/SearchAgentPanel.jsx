import { For, Show, createSignal } from "solid-js";
import FlowIcon from "../components/FlowIcon.jsx";
import { SearchAgentSettings, SearchBriefCard, SearchSourceFilters } from "./SearchAgentWorkspace.jsx";

function isBusy(phase) {
  return phase === "planning" || phase === "searching";
}

export default function SearchAgentPanel(props) {
  const controller = props.controller;
  const phase = () => controller.phase();
  const [configOpen, setConfigOpen] = createSignal(false);

  const renderConfigPanel = () => <Show when={configOpen()}>
    <section class="ual-agent-settings-panel ual-search-settings-panel is-slide-in" aria-label="素材检索配置">
      <header>
        <button class="ual-agent-settings-icon" type="button" aria-label="Back" title="Back" onClick={() => setConfigOpen(false)}>
          <FlowIcon name="arrowBack" />
        </button>
        <strong>配置</strong>
        <button class="ual-agent-settings-icon" type="button" aria-label="Close" title="Close" onClick={() => setConfigOpen(false)}>
          <FlowIcon name="close" />
        </button>
      </header>
      <div class="ual-agent-settings-body">
        <SearchAgentSettings controller={controller} />
        <div class="ual-search-agent-status">
          <strong>Agent 过程状态</strong>
          <p>{controller.statusText() || "等待输入素材需求"}</p>
          <Show when={controller.searchId()}>
            <small>{controller.searchId()}</small>
          </Show>
        </div>
        <div class="ual-search-event-list">
          <strong>Events</strong>
          <Show when={controller.eventLog().length} fallback={<p><span>idle</span><small>暂无事件</small></p>}>
            <For each={controller.eventLog()}>{(event) => <p>
              <span>{event.type}</span>
              <small>{event.provider || event.search_id || event.detail || ""}</small>
            </p>}</For>
          </Show>
        </div>
      </div>
    </section>
  </Show>;

  return <aside class="ual-agent ual-search-agent-panel">
    {renderConfigPanel()}
    <header class="ual-agent-header">
      <div class="ual-agent-title">
        <button type="button" class="ual-agent-icon" aria-label="Menu"><FlowIcon name="menu" /></button>
        <strong>Workspace</strong>
      </div>
      <div class="ual-agent-actions">
        <button class="ual-agent-icon" type="button" aria-label="Close" title="Close" onClick={props.onClose}>
          <FlowIcon name="close" />
        </button>
      </div>
    </header>
    <div class="ual-search-panel-brief">
      <SearchBriefCard controller={controller} compact />
      <SearchSourceFilters controller={controller} compact />
    </div>
    <div class="ual-search-panel-body">
      <Show when={controller.plan()}>
        <div class="ual-search-plan-card">
          <strong>检索计划</strong>
          <p>{controller.plan()?.summary || controller.plan()?.style || "结构化检索计划已生成"}</p>
          <div>
            <For each={controller.plan()?.queries || []}>{(query) => <span>{query.media_type} · {query.query}</span>}</For>
          </div>
        </div>
      </Show>
    </div>
    <footer class="ual-opencode-agent-composer ual-search-composer">
      <div class="ual-composer-box">
        <div class="ual-search-composer-quick-actions">
          <button type="button" disabled={!controller.searchText().trim() || isBusy(phase())} onClick={() => controller.createPlan()}>
            <FlowIcon name="addNotes" /> 生成计划
          </button>
          <button type="button" disabled={isBusy(phase())} onClick={() => controller.createStoryboardPlan()}>
            <FlowIcon name="addNotes" /> 按 StoryBoard 批量
          </button>
        </div>
        <textarea
          value={controller.searchText()}
          onInput={(event) => controller.setSearchText(event.currentTarget.value)}
          placeholder="医院走廊里医生查看平板，横屏，真实纪录片风格"
          rows="4"
        />
        <div class="ual-composer-tools ual-search-composer-tools">
          <div></div>
          <div>
            <button
              class={`ual-composer-icon ual-search-config-trigger ${configOpen() ? "is-active" : ""}`}
              type="button"
              aria-label="配置选项"
              title={configOpen() ? "收起配置选项" : "展开配置选项"}
              aria-expanded={configOpen()}
              onClick={() => setConfigOpen((value) => !value)}
            >
              <FlowIcon name="tune" />
            </button>
            <button
              class="ual-composer-submit"
              type="button"
              disabled={!controller.searchText().trim() || isBusy(phase())}
              onClick={() => controller.startSearch()}
              aria-label="开始检索"
              title="开始检索"
            >
              <FlowIcon name="arrowForward" />
            </button>
          </div>
        </div>
      </div>
    </footer>
  </aside>;
}
