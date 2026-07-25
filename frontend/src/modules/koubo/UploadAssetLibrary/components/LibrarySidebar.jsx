import FlowIcon from "./FlowIcon.jsx";

export default function LibrarySidebar(props) {
  const collapsed = () => Boolean(props.collapsed?.());
  return <aside class={`ual-sidebar ${collapsed() ? "is-collapsed" : ""}`}>
    <div class="ual-sidebar-top">
      <button class="ual-back-button" type="button" title={collapsed() ? "Expand Asset Library navigation" : "Close Upload Asset Library"} onClick={() => collapsed() ? props.onExpand?.() : props.onClose?.()}>
        <FlowIcon name="arrowBack" />
      </button>
      <strong>Asset Library</strong>
    </div>
    <nav class="ual-nav" aria-label="Upload Asset Library sections">
      <button class={props.view() === "images" ? "is-active" : ""} type="button" onClick={() => props.setView("images")}>
        <span class="ual-nav-icon"><FlowIcon name="image" /></span><span class="ual-nav-label">图片素材</span>
      </button>
      <button class={props.view() === "images-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("images-agent")}>
        <span class="ual-nav-icon"><FlowIcon name="addNotes" /></span><span class="ual-nav-label">图像智能体</span>
      </button>
      <button class={props.view() === "videos" ? "is-active" : ""} type="button" onClick={() => props.setView("videos")}>
        <span class="ual-nav-icon"><FlowIcon name="video" /></span><span class="ual-nav-label">视频生成</span>
      </button>
      <button class={props.view() === "videos-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("videos-agent")}>
        <span class="ual-nav-icon"><FlowIcon name="addNotes" /></span><span class="ual-nav-label">视频智能体</span>
      </button>
      <button class={props.view() === "tts-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("tts-agent")}>
        <span class="ual-nav-icon"><FlowIcon name="audio" /></span><span class="ual-nav-label">语音智能体</span>
      </button>
      <button class={props.view() === "digital-human-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("digital-human-agent")}>
        <span class="ual-nav-icon"><FlowIcon name="video" /></span><span class="ual-nav-label">数字人智能体</span>
      </button>
      <button class={props.view() === "prompt-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("prompt-agent")}>
        <span class="ual-nav-icon"><FlowIcon name="addNotes" /></span><span class="ual-nav-label">提示词智能体</span>
      </button>
      <button class={props.view() === "search-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("search-agent")}>
        <span class="ual-nav-icon"><FlowIcon name="search" /></span><span class="ual-nav-label">素材检索智能体</span>
      </button>
    </nav>
    <div class="ual-sidebar-bottom">
      <button class={props.view() === "history" ? "is-active" : ""} type="button" onClick={() => props.setView("history")}>
        <span class="ual-nav-icon"><FlowIcon name="history" /></span><span class="ual-nav-label">History</span>
      </button>
      <button type="button" title={collapsed() ? "Expand" : "Collapse"} onClick={() => collapsed() ? props.onExpand?.() : props.onCollapse?.()}>
        <span class="ual-nav-icon"><FlowIcon name="leftPanelClose" /></span><span class="ual-nav-label">Collapse</span>
      </button>
    </div>
  </aside>;
}
