import { createMemo, For, Show } from "solid-js";
import {
  costBasisLabel,
  formatMeteringTime,
  formatMicrosUsd,
  formatOptionalMicrosUsd,
  formatPriceLines,
  formatUnitMap,
  meteringStatusLabel,
  meteringTaskMeta,
  meteringTaskTitle,
  meteringWarningMessage,
  unitLabel,
} from "../lib/meteringFormat.js";
import { statusVariant } from "../shell/appShellUtils.jsx";

export default function MeteringPage(props) {
  const {
    meteringReport,
    meteringTaskReport,
    meteringDays,
    setMeteringDays,
    meteringBusy,
    loadMeteringReport,
    meteringTaskId,
    selectMeteringTask,
    meteringAttemptScope,
    loadMeteringTaskReport,
    meteringError,
    meteringTab,
    setMeteringTab
  } = props;
    const report = createMemo(() => meteringReport() ?? {});
    const taskReport = createMemo(() => meteringTaskReport());
    const totals = createMemo(() => report()?.totals ?? {});
    const taskRows = createMemo(() => report()?.by_task ?? []);
    const providerRows = createMemo(() => report()?.by_provider_model ?? []);
    const modalityRows = createMemo(() => report()?.by_modality ?? []);
    const pricebookRows = createMemo(() => report()?.pricebook ?? []);
    const items = createMemo(() => report()?.items ?? []);
    const taskTotals = createMemo(() => taskReport()?.totals ?? {});
    const actionRows = createMemo(() => taskReport()?.by_action ?? []);
    const rawRows = createMemo(() => taskReport()?.items ?? []);
    const taskWarnings = createMemo(() => taskReport()?.warnings ?? []);
    const attempts = createMemo(() => taskReport()?.attempts ?? []);
    const renderMeteringDashboard = () => {
        const renderSectionIntro = (title, description) => (<div class="metering-section-intro">
          <strong>{title}</strong>
          <p>{description}</p>
        </div>);
        const renderTaskBilling = () => (<>
          <section class="jobs-panel metering-panel">
            <div class="jobs-panel-header">
              <div>
                <h3>任务费用列表</h3>
                <p class="metering-panel-explainer">这里直接列出当前时间窗口内每个任务的费用概览。点击某一行后，下方会显示该 task 的动作拆分和原始计费记录。</p>
              </div>
              <div class="metering-header-actions">
                <label class="metering-window-select">
                  <span>时间窗口</span>
                  <select value={String(meteringDays())} onChange={(event) => setMeteringDays(Number(event.currentTarget.value))}>
                    <option value="7">近 7 天</option>
                    <option value="30">近 30 天</option>
                    <option value="90">近 90 天</option>
                    <option value="365">近 365 天</option>
                  </select>
                </label>
                <button class="secondary" type="button" disabled={meteringBusy()} onClick={() => void loadMeteringReport()}>{meteringBusy() ? "加载中..." : "刷新"}</button>
                <span class="mini-pill">{taskRows().length} 个任务</span>
              </div>
            </div>
            <div class="metering-table metering-task-table">
              <div class="metering-table-head">
                <span>计费 Task ID</span>
                <span>标题 / 运行信息</span>
                <span>请求数</span>
                <span>供应商成本</span>
                <span>估算成本</span>
                <span>用户计费</span>
                <span>毛利</span>
                <span>用量</span>
              </div>
              <Show when={taskRows().length > 0} fallback={<div class="jobs-empty metering-empty">{meteringBusy() ? "加载中..." : "当前时间窗口内还没有 task 计费概览。"}</div>}>
                <For each={taskRows()}>{(row) => (<button class={`metering-table-row metering-task-table-row ${String(row.task_id) === String(meteringTaskId()) ? "is-selected" : ""}`} type="button" onClick={() => void selectMeteringTask(row.task_id)}>
                  <span class="metering-task-id">#{row.task_id}</span>
                  <span>
                    <strong>{meteringTaskTitle(row)}</strong>
                    <small>{meteringTaskMeta(row)}</small>
                  </span>
                  <span>{Number(row.request_count ?? 0).toLocaleString()}</span>
                  <span>{formatMicrosUsd(row.provider_cost_micros ?? row.cost_micros)}</span>
                  <span>{formatMicrosUsd(row.estimated_cost_micros)}</span>
                  <span>{formatMicrosUsd(row.charge_micros ?? row.sell_micros)}</span>
                  <span class="metering-profit">{formatMicrosUsd(row.profit_micros)}</span>
                  <span>{formatUnitMap(row.units)}</span>
                </button>)}</For>
              </Show>
            </div>
          </section>
          <Show when={taskWarnings().length > 0}>
            <div class="metering-warning-list">
              <For each={taskWarnings()}>{(warning) => <div class={`banner ${warning.severity === "warning" ? "bad" : "info"}`}>{meteringWarningMessage(warning)}</div>}</For>
            </div>
          </Show>
          <Show when={taskReport()} fallback={<div class="jobs-empty metering-empty">从上方任务费用列表选择一个 task，查看它的动作拆分和原始计费记录。</div>}>
            <Show when={taskReport()?.found} fallback={<div class="jobs-empty metering-empty">没有找到这个任务。</div>}>
              <section class="jobs-panel metering-panel metering-task-summary">
                <div class="jobs-panel-header">
                  <div>
                    <h3>任务计费汇总</h3>
                    <p>Task #{taskReport()?.task?.task_id} · {meteringTaskTitle(taskReport()?.task)} · {meteringTaskMeta(taskReport()?.task)}</p>
                    <p class="metering-panel-explainer">这里是 task 的总体账单。供应商成本是实际扣费或可确认成本，估算成本是按本地价目表计算的成本，用户计费是本系统对用户收取的金额，毛利是用户计费减成本后的差额。</p>
                  </div>
                  <div class="metering-header-actions">
                    <label class="metering-window-select">
                      <span>运行范围</span>
                      <select value={meteringAttemptScope()} onChange={(event) => void loadMeteringTaskReport(meteringTaskId(), event.currentTarget.value)}>
                        <option value="all">全部运行</option>
                        <option value="latest">最新运行</option>
                        <For each={attempts()}>{(attempt) => <option value={String(attempt.id)}>第 {attempt.attempt_no || "-"} 次运行（attempt #{attempt.id}，{meteringStatusLabel(attempt.status)}）</option>}</For>
                      </select>
                    </label>
                    <span class="mini-pill">{taskReport()?.attempt_scope?.mode === "all" ? "全部运行" : taskReport()?.attempt_scope?.mode === "latest" ? "最新运行" : "指定运行"}</span>
                  </div>
                </div>
                <div class="jobs-metrics-grid metering-metrics-grid">
                  <div class="jobs-metric-card"><label>请求数</label><strong>{Number(taskTotals().request_count ?? 0).toLocaleString()}</strong></div>
                  <div class="jobs-metric-card"><label>供应商成本</label><strong>{formatMicrosUsd(taskTotals().provider_cost_micros ?? taskTotals().cost_micros)}</strong><small>{Number(taskTotals().actual_cost_count ?? 0).toLocaleString()} 条实际成本</small></div>
                  <div class="jobs-metric-card"><label>估算成本</label><strong>{formatMicrosUsd(taskTotals().estimated_cost_micros)}</strong><small>{Number(taskTotals().estimated_cost_count ?? 0).toLocaleString()} 条估算成本</small></div>
                  <div class="jobs-metric-card"><label>用户计费</label><strong>{formatMicrosUsd(taskTotals().charge_micros ?? taskTotals().sell_micros)}</strong></div>
                  <div class="jobs-metric-card"><label>毛利</label><strong>{formatMicrosUsd(taskTotals().profit_micros)}</strong></div>
                  <div class="jobs-metric-card metering-wide-metric"><label>用量</label><strong>{formatUnitMap(taskTotals().units)}</strong></div>
                </div>
              </section>
              <section class="jobs-panel metering-panel">
                <div class="jobs-panel-header">
                  <div>
                    <h3>按动作拆分</h3>
                    <p class="metering-panel-explainer">把同一个 step/action 的调用聚合到一行，适合快速判断 task 中哪一步最耗钱、哪些动作只有估算或未定价。</p>
                  </div>
                  <span class="mini-pill">{actionRows().length} 个动作</span>
                </div>
                <div class="metering-table metering-action-table">
                  <div class="metering-table-head">
                    <span>步骤</span>
                    <span>动作</span>
                    <span>类型</span>
                    <span>请求数</span>
                    <span>用量</span>
                    <span>计价依据</span>
                    <span>供应商成本</span>
                    <span>用户计费</span>
                    <span>毛利</span>
                  </div>
                  <Show when={actionRows().length > 0} fallback={<div class="jobs-empty metering-empty">当前任务范围内没有可计费动作。</div>}>
                    <For each={actionRows()}>{(row) => (<div class="metering-table-row">
                      <span>{row.step_id || "-"}</span>
                      <span class="metering-model-cell">{row.action || row.model_id || "-"}</span>
                      <span>{row.modality || "-"}</span>
                      <span>{Number(row.request_count ?? 0).toLocaleString()}</span>
                      <span>{formatUnitMap(row.units)}</span>
                      <span>{Object.entries(row.cost_basis_counts || {}).map(([key, value]) => `${costBasisLabel(key)} ${value}`).join(" · ") || "-"}</span>
                      <span>{formatMicrosUsd(row.provider_cost_micros ?? row.cost_micros)}</span>
                      <span>{formatMicrosUsd(row.charge_micros ?? row.sell_micros)}</span>
                      <span class="metering-profit">{formatMicrosUsd(row.profit_micros)}</span>
                    </div>)}</For>
                  </Show>
                </div>
              </section>
              <section class="jobs-panel metering-panel">
                <div class="jobs-panel-header">
                  <div>
                    <h3>原始计费记录</h3>
                    <p class="metering-panel-explainer">逐条列出写入计费日志的原始调用。排查争议账单时，从这里看具体时间、模型、用量、成本来源和 usage id。</p>
                  </div>
                  <span class="mini-pill">{rawRows().length} 条记录</span>
                </div>
                <div class="metering-table metering-raw-table">
                  <div class="metering-table-head">
                    <span>时间</span>
                    <span>attempt</span>
                    <span>步骤</span>
                    <span>供应商</span>
                    <span>模型</span>
                    <span>类型</span>
                    <span>状态</span>
                    <span>用量</span>
                    <span>计价依据</span>
                    <span>供应商成本</span>
                    <span>用户计费</span>
                    <span>毛利</span>
                    <span>记录 ID</span>
                  </div>
                  <Show when={rawRows().length > 0} fallback={<div class="jobs-empty metering-empty">当前任务范围内没有原始计费记录。</div>}>
                    <For each={rawRows()}>{(item) => (<div class="metering-table-row">
                      <span>{formatMeteringTime(item.created_at ?? item.finished_at ?? item.started_at)}</span>
                      <span>{item.attempt_id || "-"}</span>
                      <span>{item.step_id || "-"}</span>
                      <span>{item.provider || "-"}</span>
                      <span class="metering-model-cell">{item.model_id || "-"}</span>
                      <span>{item.modality || "-"}</span>
                      <span><span class={`status-tag tag-${statusVariant(item.status || "unknown")}`}>{meteringStatusLabel(item.status)}</span></span>
                      <span>{formatUnitMap(item.units)}</span>
                      <span><span class={`metering-basis metering-basis-${item.cost_basis || "none"}`}>{costBasisLabel(item.cost_basis)}</span></span>
                      <span>{formatMicrosUsd(item.provider_cost_micros ?? item.cost_micros)}</span>
                      <span>{formatMicrosUsd(item.charge_micros ?? item.sell_micros)}</span>
                      <span class="metering-profit">{formatMicrosUsd(item.profit_micros)}</span>
                      <span>{item.id || "-"}</span>
                    </div>)}</For>
                  </Show>
                </div>
              </section>
            </Show>
          </Show>
        </>);
        const renderGlobalUsage = () => (<>
          <section class="jobs-panel metering-panel metering-filter-panel">
            {renderSectionIntro("全局用量筛选", "这里按时间窗口汇总所有本地计费记录，适合查看最近一段时间整体成本、收入和利润走势。切换窗口后点击刷新。")}
            <div class="metering-toolbar">
            <div class="jobs-nav-actions">
              <label class="metering-window-select">
                <span>时间窗口</span>
                <select value={String(meteringDays())} onChange={(event) => setMeteringDays(Number(event.currentTarget.value))}>
                  <option value="7">近 7 天</option>
                  <option value="30">近 30 天</option>
                  <option value="90">近 90 天</option>
                  <option value="365">近 365 天</option>
                </select>
              </label>
              <button class="secondary" type="button" disabled={meteringBusy()} onClick={() => void loadMeteringReport()}>{meteringBusy() ? "加载中..." : "刷新"}</button>
            </div>
            </div>
          </section>
          <section class="jobs-panel metering-panel metering-summary-panel">
            <div class="jobs-panel-header">
              <div>
                <h3>全局汇总指标</h3>
                <p class="metering-panel-explainer">这里汇总当前时间窗口内所有计费记录。它反映整体经营数据，不区分具体 task；如果要回答某个用户任务的账单，请优先看“任务计费”。</p>
              </div>
            </div>
            <div class="jobs-metrics-grid metering-metrics-grid">
              <div class="jobs-metric-card"><label>请求数</label><strong>{Number(totals().request_count ?? 0).toLocaleString()}</strong></div>
              <div class="jobs-metric-card"><label>实际 API 成本</label><strong>{formatOptionalMicrosUsd(totals().actual_cost_micros, Number(totals().actual_cost_count ?? 0) > 0)}</strong><small>{Number(totals().actual_cost_count ?? 0).toLocaleString()} 条实际成本</small></div>
              <div class="jobs-metric-card"><label>估算 API 成本</label><strong>{formatMicrosUsd(totals().estimated_cost_micros)}</strong><small>{Number(totals().estimated_cost_count ?? 0).toLocaleString()} 条估算成本</small></div>
              <div class="jobs-metric-card"><label>用户计费</label><strong>{formatMicrosUsd(totals().charge_micros ?? totals().sell_micros)}</strong></div>
              <div class="jobs-metric-card"><label>毛利</label><strong>{formatMicrosUsd(totals().profit_micros)}</strong></div>
              <div class="jobs-metric-card metering-wide-metric"><label>材料 / 用量</label><strong>{formatUnitMap(totals().units)}</strong></div>
            </div>
          </section>
          <section class="jobs-panel metering-panel">
            <div class="jobs-panel-header">
              <div>
                <h3>按供应商 / 模型汇总</h3>
                <p class="metering-panel-explainer">按 provider、model 和调用类型聚合，适合比较不同模型的成本、定价和利润贡献。</p>
              </div>
              <span class="mini-pill">{providerRows().length} 组</span>
            </div>
            <div class="metering-table metering-provider-table">
              <div class="metering-table-head">
                <span>供应商</span>
                <span>模型</span>
                <span>类型</span>
                <span>用量</span>
                <span>API 单价</span>
                <span>实际成本</span>
                <span>估算成本</span>
                <span>用户计费</span>
                <span>毛利</span>
              </div>
              <Show when={providerRows().length > 0} fallback={<div class="jobs-empty metering-empty">当前时间窗口内没有本地计费记录。</div>}>
                <For each={providerRows()}>{(row) => (<div class="metering-table-row">
                    <span>{row.provider || "-"}</span>
                    <span class="metering-model-cell">{row.model_id || "-"}</span>
                    <span>{row.modality || "-"}</span>
                    <span>{formatUnitMap(row.units)}</span>
                    <span>{formatPriceLines(row.price_lines)}</span>
                    <span>{formatOptionalMicrosUsd(row.actual_cost_micros, Number(row.actual_cost_count ?? 0) > 0)}</span>
                    <span>{formatMicrosUsd(row.estimated_cost_micros)}</span>
                    <span>{formatMicrosUsd(row.charge_micros ?? row.sell_micros)}</span>
                    <span class="metering-profit">{formatMicrosUsd(row.profit_micros)}</span>
                  </div>)}</For>
              </Show>
            </div>
          </section>
          <section class="jobs-panel metering-panel">
            <div class="jobs-panel-header">
              <div>
                <h3>按调用类型汇总</h3>
                <p class="metering-panel-explainer">按文本、图片、视频、语音、本地产物等类型聚合，用来判断成本主要来自哪类能力。</p>
              </div>
              <span class="mini-pill">{modalityRows().length} 组</span>
            </div>
            <div class="metering-table metering-modality-table">
              <div class="metering-table-head">
                <span>类型</span>
                <span>请求数</span>
                <span>用量</span>
                <span>实际成本</span>
                <span>估算成本</span>
                <span>用户计费</span>
                <span>毛利</span>
              </div>
              <Show when={modalityRows().length > 0} fallback={<div class="jobs-empty metering-empty">还没有可按类型汇总的用量。</div>}>
                <For each={modalityRows()}>{(row) => (<div class="metering-table-row">
                    <span>{row.modality || "-"}</span>
                    <span>{Number(row.request_count ?? 0).toLocaleString()}</span>
                    <span>{formatUnitMap(row.units)}</span>
                    <span>{formatOptionalMicrosUsd(row.actual_cost_micros, Number(row.actual_cost_count ?? 0) > 0)}</span>
                    <span>{formatMicrosUsd(row.estimated_cost_micros)}</span>
                    <span>{formatMicrosUsd(row.charge_micros ?? row.sell_micros)}</span>
                    <span class="metering-profit">{formatMicrosUsd(row.profit_micros)}</span>
                  </div>)}</For>
              </Show>
            </div>
          </section>
          <section class="jobs-panel metering-panel">
            <div class="jobs-panel-header">
              <div>
                <h3>全局明细</h3>
                <p class="metering-panel-explainer">当前时间窗口内的原始计费记录。它用于追溯单次调用，不建议用它替代“按任务查看计费”。</p>
              </div>
              <span class="mini-pill">{items().length} 条记录</span>
            </div>
            <div class="metering-table metering-detail-table">
              <div class="metering-table-head">
                <span>时间</span>
                <span>供应商</span>
                <span>模型</span>
                <span>类型</span>
                <span>状态</span>
                <span>用量</span>
                <span>API 单价</span>
                <span>计价依据</span>
                <span>实际成本</span>
                <span>估算成本</span>
                <span>用户计费</span>
                <span>毛利</span>
              </div>
              <Show when={items().length > 0} fallback={<div class="jobs-empty metering-empty">当前时间窗口内没有计费明细。</div>}>
                <For each={items()}>{(item) => (<div class="metering-table-row">
                    <span>{formatMeteringTime(item.created_at ?? item.finished_at ?? item.started_at)}</span>
                    <span>{item.provider || "-"}</span>
                    <span class="metering-model-cell">{item.model_id || "-"}</span>
                    <span>{item.modality || "-"}</span>
                    <span><span class={`status-tag tag-${statusVariant(item.status || "unknown")}`}>{meteringStatusLabel(item.status)}</span></span>
                    <span>{formatUnitMap(item.units)}</span>
                    <span>{formatPriceLines(item.unit_lines)}</span>
                    <span><span class={`metering-basis metering-basis-${item.cost_basis || "none"}`}>{costBasisLabel(item.cost_basis)}</span></span>
                    <span>{formatOptionalMicrosUsd(item.actual_cost_micros, item.has_actual_cost)}</span>
                    <span>{formatMicrosUsd(item.estimated_cost_micros)}</span>
                    <span>{formatMicrosUsd(item.charge_micros ?? item.sell_micros)}</span>
                    <span class="metering-profit">{formatMicrosUsd(item.profit_micros)}</span>
                  </div>)}</For>
              </Show>
            </div>
          </section>
        </>);
        const renderPriceRules = () => (<section class="jobs-panel metering-panel">
          <div class="jobs-panel-header">
            <div>
              <h3>价格规则</h3>
              <p class="metering-panel-explainer">这里列出本地价目表。供应商没有返回实际成本时，系统会用这些规则估算成本，并按“用户计费单价”计算应收金额。</p>
            </div>
            <span class="mini-pill">{pricebookRows().length} 条规则</span>
          </div>
          <div class="metering-table metering-pricebook-table">
            <div class="metering-table-head">
              <span>供应商</span>
              <span>模型</span>
              <span>类型</span>
              <span>计费单位</span>
              <span>供应商单价</span>
              <span>用户计费单价</span>
              <span>来源</span>
            </div>
            <Show when={pricebookRows().length > 0} fallback={<div class="jobs-empty metering-empty">还没有配置价格规则。</div>}>
              <For each={pricebookRows()}>{(row) => (<div class="metering-table-row">
                  <span>{row.provider || "-"}</span>
                  <span class="metering-model-cell">{row.model_id || "-"}</span>
                  <span>{row.modality || "-"}</span>
                  <span>{unitLabel(row.unit_key)}</span>
                  <span>{formatMicrosUsd(row.provider_unit_cost_micros)}</span>
                  <span>{formatMicrosUsd(row.customer_unit_price_micros)}</span>
                  <span>{row.source || "-"}</span>
                </div>)}</For>
            </Show>
          </div>
        </section>);
        return (<div class="metering-page">
          <div class="metering-tabs">
            <button class={`metering-tab ${meteringTab() === "task" ? "active" : ""}`} type="button" onClick={() => {
                setMeteringTab("task");
                const currentTaskId = meteringTaskId();
                window.location.hash = currentTaskId ? `#/metering/task/${currentTaskId}` : "#/metering";
            }}>任务计费</button>
            <button class={`metering-tab ${meteringTab() === "global" ? "active" : ""}`} type="button" onClick={() => {
                setMeteringTab("global");
                window.location.hash = "#/metering/global";
                if (!meteringReport())
                    void loadMeteringReport();
            }}>全局用量</button>
            <button class={`metering-tab ${meteringTab() === "price" ? "active" : ""}`} type="button" onClick={() => {
                setMeteringTab("price");
                window.location.hash = "#/metering/prices";
                if (!meteringReport())
                    void loadMeteringReport();
            }}>价格规则</button>
          </div>
          <Show when={meteringError()}>
            <div class="banner bad">{meteringError()}</div>
          </Show>
          <Show when={meteringTab() === "task"}>{renderTaskBilling()}</Show>
          <Show when={meteringTab() === "global"}>{renderGlobalUsage()}</Show>
          <Show when={meteringTab() === "price"}>{renderPriceRules()}</Show>
          <div class="metering-note">说明：如果供应商返回实际成本，优先使用实际成本；没有实际成本时使用本地价目表估算。原始用量是核算依据。</div>
        </div>);
    };

  return renderMeteringDashboard();
}
