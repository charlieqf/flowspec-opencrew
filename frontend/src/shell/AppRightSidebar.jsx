import { Show, createEffect, createSignal } from "solid-js";
import { formatMicrosUsd } from "../lib/meteringFormat.js";
import { StatusBadge } from "./appShellUtils.jsx";

export default function AppRightSidebar(props) {
  const {
    activeNav,
    startRightResize,
    AnalysisV1MediaSidebar,
    analysisV1MediaItem,
    DanceMimicV1MediaSidebar,
    danceMimicMediaItem,
    TalkingHeadV1MediaSidebar,
    talkingHeadMediaItem,
    kouboStoryBoardSidebar,
    meteringReport,
    meteringDays,
    sessionTaskSummary,
    selectedSession,
    state,
    npcStepStatus,
    publishStepStatus
  } = props;
  const [mobileOpen, setMobileOpen] = createSignal(false);
  createEffect(() => {
    if (activeNav() !== "koubo-storyboard" || !kouboStoryBoardSidebar()) {
      setMobileOpen(false);
    }
  });
  return (
    <>
      <Show when={activeNav() === "koubo-storyboard" && kouboStoryBoardSidebar()}>
        <button
          class="right-mobile-toggle"
          type="button"
          aria-label="打开素材面板"
          aria-expanded={mobileOpen()}
          onClick={() => setMobileOpen(true)}
        >素材面板</button>
      </Show>
      <aside class={`right ${mobileOpen() ? "is-mobile-open" : ""}`}>
        <Show when={activeNav() === "koubo-storyboard" && kouboStoryBoardSidebar()}>
          <button
            class="right-mobile-close"
            type="button"
            aria-label="关闭素材面板"
            onClick={() => setMobileOpen(false)}
          >关闭</button>
        </Show>
        <Show when={activeNav() === "analysis-v1" || activeNav() === "dance-mimic" || activeNav() === "talking-head" || activeNav() === "koubo-storyboard"}>
          <button class="right-resize-grip" onMouseDown={startRightResize} aria-label="Resize right sidebar"/>
        </Show>
        <Show when={activeNav() === "connection"} fallback={activeNav() === "analysis-v1" ? <AnalysisV1MediaSidebar item={analysisV1MediaItem()}/> : activeNav() === "dance-mimic" ? <DanceMimicV1MediaSidebar item={danceMimicMediaItem()} /> : activeNav() === "talking-head" ? <TalkingHeadV1MediaSidebar item={talkingHeadMediaItem()} /> : activeNav() === "koubo-storyboard" && kouboStoryBoardSidebar() ? kouboStoryBoardSidebar() : activeNav() === "metering" ? <section class="panel">
            <h3>计费快照</h3>
            <ul>
              <li><label>请求数</label><span>{String(meteringReport()?.totals?.request_count ?? 0)}</span></li>
              <li><label>成本</label><span>{formatMicrosUsd(meteringReport()?.totals?.cost_micros)}</span></li>
              <li><label>计费</label><span>{formatMicrosUsd(meteringReport()?.totals?.sell_micros)}</span></li>
              <li><label>毛利</label><span>{formatMicrosUsd(meteringReport()?.totals?.profit_micros)}</span></li>
              <li><label>窗口</label><span>{meteringDays()} 天</span></li>
            </ul>
          </section> : <section class="panel">
            <h3>System Health</h3>
            <ul>
              <li><label>Total</label><span>{String(sessionTaskSummary()?.total ?? 0)}</span></li>
              <li><label>Running</label><span>{String(sessionTaskSummary()?.running ?? 0)}</span></li>
              <li><label>CPU</label><span>{(sessionTaskSummary()?.cpu_percent ?? 0).toFixed(1)}%</span></li>
              <li><label>Memory</label><span>{(sessionTaskSummary()?.memory_percent ?? 0).toFixed(1)}%</span></li>
              <li><label>Active</label><span>{selectedSession()?.title ?? "-"}</span></li>
            </ul>
          </section>}>
          <section class="panel">
            <h3>Connection Health</h3>
            <ul>
              <li><label>OpenCode</label><StatusBadge status={state()?.opencode?.status}/></li>
              <li><label>NPC</label><StatusBadge status={npcStepStatus()}/></li>
              <li><label>URL</label><StatusBadge status={publishStepStatus()}/></li>
            </ul>
          </section>
        </Show>

      </aside>
    </>
  );
}
