import { For } from "solid-js";
import FlowIcon from "../components/FlowIcon.jsx";
import "./ttsAgent.css";

function displayMessageText(value) {
  const source = String(value || "").trim();
  if (!source || source === "Not Found") return "";
  if (/声音文件已生成到\s+Asset Audio\s+文件夹/.test(source)) {
    return "声音文件已生成到 Asset Audio，可以直接使用。";
  }
  return source.replace(/输出[:：]\s*SessionOutput\/[^\s]+/g, "").trim();
}

function messageTone(message) {
  const content = displayMessageText(message?.text);
  if (/已套用|已加载|已生成到 Asset Audio|已创建/.test(content)) return "status";
  return message?.role === "user" ? "user" : "assistant";
}

export default function TtsAgentPanel(props) {
  const controller = props.controller;
  return <aside class="ual-agent ual-video-agent ual-tts-agent-panel is-opencode-workspace">
    <section class="ual-opencode-agent is-workspace" role="dialog" aria-label="语音智能体">
      <header class="ual-agent-header">
        <div class="ual-agent-title">
          <button type="button" class="ual-agent-icon" aria-label="Menu"><FlowIcon name="menu" /></button>
          <strong>Workspace</strong>
        </div>
      </header>
      <section class="ual-opencode-agent-chat ual-tts-agent-chat">
        <For each={controller.messages().map((message) => ({ ...message, displayText: displayMessageText(message.text), tone: messageTone(message) })).filter((message) => message.displayText)}>{(message) => (
          <article class={`ual-message ual-tts-message is-${message.role} is-${message.tone}`}>
            <div class={message.role === "user" ? "ual-user-bubble" : "ual-assistant-bubble"}>
              <span class="ual-tts-message-label">{message.role === "user" ? "YOU" : "AGENT"}</span>
              <p>{message.displayText}</p>
            </div>
          </article>
        )}</For>
      </section>
      <form class="ual-opencode-agent-composer ual-tts-composer" onSubmit={(event) => {
        event.preventDefault();
        controller.submitRequest(controller.requestText());
      }}>
        <div class="ual-composer-box">
          <textarea value={controller.requestText()} rows="3" onInput={(event) => controller.setRequestText(event.currentTarget.value)} placeholder="输入 TTS 需求..." />
          <div class="ual-composer-tools">
            <span class="ual-tts-agent-state"><i></i>{controller.audioState() === "generating" ? "generating" : "real"}</span>
            <div>
              <button type="button" class="ual-tts-generate-button" disabled={Boolean(controller.generateDisabledReason())} title={controller.generateDisabledReason() || "生成声音"} onClick={() => void controller.generateAudio()}>
                {controller.audioState() === "generating" ? "生成中" : "生成声音"}
              </button>
              <button type="submit" class="ual-composer-submit" title="Send" aria-label="Send TTS request">
                <FlowIcon name="arrowForward" />
              </button>
            </div>
          </div>
        </div>
      </form>
    </section>
  </aside>;
}
