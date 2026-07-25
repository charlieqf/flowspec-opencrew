"""Generate four fully standalone HTML explainers from executable demo records."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from demo_runtime import DEMO_ROOT, load_json


STATUS_LABELS = {
    "completed": "已完成",
    "skipped": "分支关闭",
    "waiting": "等待中",
    "running": "运行中",
    "failed": "失败",
    "blocked": "阻断",
    "cancelled": "已取消",
}


def h(value: Any) -> str:
    return html.escape(str(value), quote=True)


def json_for_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def step_display_name(step: dict[str, Any]) -> str:
    suffix = str(step["id"]).split("_", 1)[-1]
    return suffix.replace("_", " ").title()


def step_ui(metadata: dict[str, Any], step: dict[str, Any]) -> dict[str, str]:
    localized = (metadata.get("step_labels") or {}).get(str(step["id"])) or {}
    return {
        "title": str(localized.get("title") or step_display_name(step)),
        "description": str(localized.get("description") or step.get("description") or ""),
    }


def stage_display_name(metadata: dict[str, Any], stage: str) -> str:
    return str(
        (metadata.get("stage_labels") or {}).get(stage)
        or stage.replace("_", " ").title()
    )


def outcome_display_name(metadata: dict[str, Any], outcome: dict[str, Any]) -> str:
    return str(
        (metadata.get("outcome_labels") or {}).get(str(outcome["id"]))
        or outcome.get("title")
        or outcome["id"]
    )


def _dependency_sources(dependency: dict[str, Any], kind: str = "all") -> list[tuple[str, str]]:
    if dependency.get("step_id"):
        return [(str(dependency["step_id"]), kind)]
    sources: list[tuple[str, str]] = []
    for child in dependency.get("any_of") or []:
        sources.extend(_dependency_sources(child, "any"))
    return sources


def _dependency_edges(process: dict[str, Any]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for step in process.get("steps") or []:
        target = str(step["id"])
        for dependency in step.get("depends_on") or []:
            for source, kind in _dependency_sources(dependency):
                edges.append(
                    {
                        "source": source,
                        "target": target,
                        "kind": kind,
                        "guarded": "true" if step.get("when") else "false",
                    }
                )
    return edges


def _dependency_graph_markup(process: dict[str, Any], metadata: dict[str, Any]) -> str:
    steps = process.get("steps") or []
    edges = _dependency_edges(process)
    step_by_id = {str(step["id"]): step for step in steps}
    order_by_id = {str(step["id"]): index for index, step in enumerate(steps, start=1)}
    incoming: dict[str, list[dict[str, str]]] = {step_id: [] for step_id in step_by_id}
    outgoing: dict[str, list[dict[str, str]]] = {step_id: [] for step_id in step_by_id}
    for edge in edges:
        incoming[edge["target"]].append(edge)
        outgoing[edge["source"]].append(edge)

    memo: dict[str, int] = {}

    def level(step_id: str, active: set[str] | None = None) -> int:
        if step_id in memo:
            return memo[step_id]
        active = set(active or ())
        if step_id in active:
            raise ValueError(f"cycle while laying out demo dependency graph: {step_id}")
        active.add(step_id)
        parents = [edge["source"] for edge in incoming.get(step_id) or []]
        value = 0 if not parents else max(level(parent, active) for parent in parents) + 1
        memo[step_id] = value
        return value

    grouped: dict[int, list[dict[str, Any]]] = {}
    for step in steps:
        grouped.setdefault(level(str(step["id"])), []).append(step)
    max_level = max(grouped, default=0)

    layer_markup: list[str] = []
    for layer in range(max_level + 1):
        nodes: list[str] = []
        for step in grouped.get(layer, []):
            step_id = str(step["id"])
            localized = step_ui(metadata, step)
            badges = []
            if len(outgoing[step_id]) > 1:
                badges.append('<span class="dag-role fork">fork · 一对多</span>')
            if len(incoming[step_id]) > 1:
                join_kind = "OR" if all(edge["kind"] == "any" for edge in incoming[step_id]) else "AND"
                badges.append(f'<span class="dag-role join">{join_kind}-join · 多对一</span>')
            if step.get("when"):
                badges.append('<span class="dag-role guard">guard</span>')
            nodes.append(
                f"""
                <button class="dag-map-node" type="button" data-step-id="{h(step_id)}">
                  <span class="dag-node-top"><span class="dag-order">{order_by_id[step_id]:02d}</span><code>{h(step_id)}</code><span class="dag-node-state">未运行</span></span>
                  <strong>{h(localized['title'])}</strong>
                  <small>{h(stage_display_name(metadata, str(step.get('stage') or '')))} · <code>{h(step['tool'])}</code></small>
                  <span class="dag-role-row">{''.join(badges)}</span>
                </button>
                """
            )
        layer_markup.append(
            f'<section class="dag-map-layer" data-level="{layer}"><header>LEVEL {layer:02d}</header>{"".join(nodes)}</section>'
        )

    mobile_rows: list[str] = []
    for step in steps:
        step_id = str(step["id"])
        parents = [edge["source"] for edge in incoming[step_id]]
        parent_text = "无前置，可作为根节点启动" if not parents else " + ".join(
            f"{order_by_id[parent]:02d} {step_ui(metadata, step_by_id[parent])['title']}"
            for parent in parents
        )
        roles = []
        if len(outgoing[step_id]) > 1:
            roles.append("分叉 fork")
        if len(incoming[step_id]) > 1:
            roles.append("汇合 join")
        mobile_rows.append(
            f"""
            <button class="dependency-mobile-row" type="button" data-step-id="{h(step_id)}">
              <span>{order_by_id[step_id]:02d}</span>
              <div><strong>{h(step_ui(metadata, step)['title'])}</strong><code>{h(step_id)} · {h(step['tool'])}</code><small><b>前置 → 本步</b>{h(parent_text)}</small></div>
              <em>{h(' / '.join(roles) or '普通步骤')}</em>
            </button>
            """
        )

    fork_count = sum(1 for items in outgoing.values() if len(items) > 1)
    join_count = sum(1 for items in incoming.values() if len(items) > 1)
    canvas_width = max(900, (max_level + 1) * 235 + max_level * 72)
    return f"""
      <section class="dependency-map" aria-labelledby="dependency-map-title">
        <header class="dependency-map-head"><div><p class="eyebrow">Declared dependency graph</p><h3 id="dependency-map-title">步骤次序、分叉与汇合</h3></div><p><b>编号</b>是定义/阅读顺序；<b>箭头</b>才是执行依赖。点击节点可联动下方 Step 详情。</p></header>
        <div class="dag-legend"><span><i class="legend-line"></i>必须依赖</span><span><i class="legend-line guarded"></i>带 guard 的分支</span><span>{len(steps)} 个步骤</span><span>{len(edges)} 条依赖</span><span>{fork_count} 个分叉</span><span>{join_count} 个汇合</span></div>
        <div class="dag-map-scroll" tabindex="0" aria-label="可横向滚动的 DAG 连线图">
          <div class="dag-map-canvas" style="width:{canvas_width}px">
            <svg class="dag-edge-layer" aria-hidden="true"></svg>
            <div class="dag-map-layers">{''.join(layer_markup)}</div>
          </div>
        </div>
        <div class="dependency-mobile-list">{''.join(mobile_rows)}</div>
      </section>
    """


def _stage_markup(process: dict[str, Any], metadata: dict[str, Any]) -> str:
    order_by_id = {str(step["id"]): index for index, step in enumerate(process.get("steps") or [], start=1)}
    grouped: dict[str, list[dict[str, Any]]] = {str(stage): [] for stage in process.get("stages") or []}
    for step in process.get("steps") or []:
        grouped.setdefault(str(step.get("stage") or "other"), []).append(step)
    parts: list[str] = []
    for stage_index, (stage, steps) in enumerate(grouped.items(), start=1):
        cards: list[str] = []
        for step in steps:
            localized = step_ui(metadata, step)
            badges: list[str] = [f'<span class="contract-badge side-{h(step["side_effect_class"])}">{h(step["side_effect_class"])}</span>']
            if step.get("ai_profile_ref"):
                badges.append('<span class="contract-badge badge-ai">AI profile</span>')
            if step.get("human_gate"):
                badges.append('<span class="contract-badge badge-human">human task</span>')
            if step.get("fanout"):
                badges.append('<span class="contract-badge badge-fanout">fanout</span>')
            guard = ""
            if step.get("when"):
                operator = next(key for key in ("equals", "not_equals", "in", "exists") if key in step["when"])
                guard = f'<div class="guard-line"><span>when</span> {h(step["when"]["variable"])} {h(operator)} {h(step["when"][operator])}</div>'
            cards.append(
                f"""
                <button class="step-card" type="button" data-step-id="{h(step['id'])}" aria-label="查看 {h(localized['title'])} 详情">
                  <span class="status-orb" aria-hidden="true"></span>
                  <span class="step-card-head">
                    <span class="step-order">STEP {order_by_id[str(step['id'])]:02d}</span>
                    <span class="step-seq">{h(step['id'])}</span>
                    <span class="step-type">{h(step.get('type') or 'script')}</span>
                  </span>
                  <strong>{h(localized['title'])}</strong>
                  <span class="step-desc">{h(localized['description'])}</span>
                  {guard}
                  <span class="badge-row">{''.join(badges)}</span>
                  <span class="step-runtime"><span class="runtime-status">未运行</span><span class="runtime-facts">—</span></span>
                </button>
                """
            )
        parts.append(
            f"""
            <section class="stage-column" data-stage="{h(stage)}">
              <header class="stage-head"><span>{stage_index:02d}</span><div><small>STAGE · {h(stage)}</small><h3>{h(stage_display_name(metadata, stage))}</h3></div></header>
              <div class="stage-line" aria-hidden="true"></div>
              <div class="stage-cards">{''.join(cards)}</div>
            </section>
            """
        )
    return "".join(parts)


def _outcome_markup(process: dict[str, Any], metadata: dict[str, Any]) -> str:
    cards = []
    for outcome in (process.get("completion") or {}).get("outcomes") or []:
        cards.append(
            f"""
            <article class="outcome-card" data-outcome-id="{h(outcome['id'])}">
              <span class="outcome-check">✓</span>
              <div><strong>{h(outcome_display_name(metadata, outcome))}</strong><small>{h(outcome['id'])}</small></div>
              <span class="outcome-artifact">{h(', '.join(outcome.get('required_artifacts') or []))}</span>
            </article>
            """
        )
    return "".join(cards)


def _explainer_markup(metadata: dict[str, Any]) -> str:
    explainer = metadata["scenario_explainer"]
    points = []
    for index, point in enumerate(explainer["points"], start=1):
        points.append(
            f"""
            <article class="guide-card">
              <span class="guide-number">0{index}</span>
              <h3>{h(point['feature'])}</h3>
              <div class="guide-rule"><small>规范支持</small><p>{h(point['support'])}</p></div>
              <div class="guide-rule constraint"><small>规范约束</small><p>{h(point['constraint'])}</p></div>
            </article>
            """
        )
    glossary = "".join(
        f'<div class="glossary-item"><dt>{h(item["term"])}</dt><dd>{h(item["meaning"])}</dd></div>'
        for item in explainer["glossary"]
    )
    return f"""
      <section class="scenario-guide" aria-labelledby="scenario-guide-title">
        <header class="guide-lead">
          <div><p class="eyebrow">Why FlowSpec here</p><h2 id="scenario-guide-title">{h(explainer['title'])}</h2></div>
          <p>{h(explainer['challenge'])}</p>
        </header>
        <div class="guide-grid">{''.join(points)}</div>
        <aside class="guide-effect"><small>FlowSpec 在本场景中的作用</small><p>{h(explainer['effect'])}</p></aside>
        <div class="audience-paths"><span><b>决策层建议</b> 先看本导读、流程图和 outcome，理解业务控制与可追责结果。</span><span><b>开发团队建议</b> 再下钻运行记录、Artifact、AI/成本治理和规范透视，核对实现契约。</span></div>
        <div class="glossary-block"><header><small>Executive glossary</small><h3>结合本场景看懂关键术语</h3></header><dl>{glossary}</dl></div>
      </section>
    """


def _executive_tldr_markup(metadata: dict[str, Any]) -> str:
    summary = metadata["executive_tldr"]
    return f"""
      <aside class="shell executive-tldr" aria-label="决策层摘要">
        <span class="tldr-kicker">Executive TL;DR</span>
        <p>
          <span class="tldr-clause"><b>业务结果</b>{h(summary['result'])}</span>
          <span class="tldr-clause"><b>控制保证</b>{h(summary['control'])}</span>
          <span class="tldr-clause tldr-boundary"><b>演示边界</b>{h(summary['boundary'])}</span>
        </p>
      </aside>
    """


def _run_control_markup(metadata: dict[str, Any]) -> str:
    examples = metadata["run_control_examples"]
    cards = [
        ("01", "full", "运行整个流程", examples["full"], "激活根节点 → exactly-one outcome"),
        ("02", "through_step", "运行到某步骤", examples["through"], "目标的前置闭包 → 目标 → waiting(user)"),
        ("03", "from_step", "从某步骤开始重跑", examples["from"], "新 Run · 所选节点 + 受影响后继"),
        ("04", "only_step", "重新单独运行某步骤", examples["single"], "diagnostic · 不发布 canonical Manifest"),
    ]
    markup = []
    for number, mode, title, example, rule in cards:
        markup.append(
            f"""
            <article class="run-control-card">
              <header><span>{number}</span><code>{h(mode)}</code></header>
              <h3>{h(title)}</h3>
              <p>{h(example)}</p>
              <small>{h(rule)}</small>
            </article>
            """
        )
    return f"""
      <section class="run-control-panel" aria-labelledby="run-control-title">
        <header class="run-control-head"><div><p class="eyebrow">User-selected execution scope</p><h2 id="run-control-title">临时决定怎么跑，也要服从同一张依赖图</h2></div><div><span>契约投影 · 非操作按钮</span><p>本 Mock Runtime 只物化完整 Run；下列卡片展示目标控制语义，不伪装成已经可点击的通用引擎能力。</p></div></header>
        <div class="run-control-grid">{''.join(markup)}</div>
        <aside class="run-control-note"><b>关键边界</b><span>“运行到”按目标的前置闭包；“从这里重跑”按受影响后继闭包。复用只针对 canonical Artifact，并校验 producer Attempt 输入摘要、schema/content hash、按需 binding 与冻结 Tool/Profile；单步正式采用结果时必须使下游失效并转为 <code>from_step</code>。</span></aside>
      </section>
    """


def _storage_markup() -> str:
    levels = [
        ("debug", "细粒度判断；生产默认关闭或短期采样"),
        ("info", "Attempt 开始/结束、adapter 与发布摘要"),
        ("warning", "可恢复降级、截断、预算或容量接近阈值"),
        ("error", "当前操作失败；另写 Error 与 durable Event"),
        ("critical", "完整性、权限、安全或平台级故障，立即告警"),
    ]
    level_markup = "".join(
        f'<article class="log-level level-{h(level)}"><code>{h(level)}</code><p>{h(description)}</p></article>'
        for level, description in levels
    )
    return f"""
      <div class="section-head"><div><p class="eyebrow">Physical evidence contract</p><h2>日志落哪里，输入输出放哪里</h2></div><p>逻辑身份必须能落到可审计的物理位置，但不能为 session、task、Run 和 Step 各复制一套文件。这里同时展示权威存储、OpenCrew 兼容目录和本 Demo 的真实 mock 证据。</p></div>
      <aside class="storage-boundary"><b>兼容边界</b><span>这是规范与 Demo 的目标投影，不会修改 OpenCrew 代码或现有 Workspace。保留 <code>tool_use_sessions / 0_SessionContext / S{{index}}_{{tool}} / Working|Output|Report|Prompt</code>；只在既有目录内增加 Attempt 分区和发布前 staging 约定。</span></aside>
      <div class="authority-grid">
        <article class="authority-plane"><small>DATABASE · durable</small><h3>状态、审计与费用事实</h3><p><code>session_events</code> 保存业务/状态事件；Usage/Cost 进入计量表或 ledger；task 是数据库记录并引用 session Workspace。</p><b>不把高容量 stdout/stderr 塞进数据库</b></article>
        <article class="authority-plane"><small>WORKSPACE / OBJECT STORE</small><h3>Artifact 与 Attempt 诊断</h3><p>输入快照、Step Artifact、Manifest、Prompt 和按 Attempt 隔离的诊断文件位于 Workspace；大文件可使用带版本 URI。</p><b>文件存在不等于 Artifact 有效</b></article>
        <article class="authority-plane"><small>PLATFORM LOG SINK</small><h3>服务与进程日志</h3><p>API、worker、scheduler 与数据库日志位于 session Workspace 之外，由部署配置轮转、保留和告警。</p><b>本地 /tmp 路径不是业务契约</b></article>
        <article class="authority-plane"><small>PROJECTION · rebuildable</small><h3>UI tail 与 SessionOutput</h3><p>UI 日志尾部和 <code>SessionOutput</code> 是便于查看/下载的投影，必须能反向指到 Event、Attempt 或 canonical Artifact。</p><b>投影不是第二权威源</b></article>
      </div>
      <div class="storage-layout-grid">
        <article class="panel-card storage-tree-card"><h3>OpenCrew 兼容物理目录</h3><p><code>&lt;workspace&gt;</code> 由 storage resolver / <code>sessions.workspace_dir</code> 解析；task 不另建文件树。Step 输入默认引用 Run 输入清单和上游 Manifest，不复制大文件。</p><pre class="storage-tree">&lt;workspace&gt;/
├─ inbox/                                  # session 原始输入（推荐）
├─ SessionContext | SessionOutput | SessionReport
└─ tool_use_sessions/&lt;run_id&gt;/
   ├─ 0_SessionContext/
   │  ├─ Variables.json
   │  ├─ InputManifest.json                # Run 冻结输入
   │  └─ &lt;declared inputs...&gt;
   ├─ S{{index}}_{{tool}}/                  # <span id="storage-example-step">当前 Step 示例</span>
   │  ├─ State.json
   │  ├─ Working/Attempts/A{{no}}_{{attempt}}/
   │  ├─ Report/Attempts/A{{no}}_{{attempt}}/
   │  │  ├─ diagnostic.ndjson
   │  │  ├─ stdout.log
   │  │  └─ stderr.log
   │  ├─ Prompt/Attempts/A{{no}}_{{attempt}}/
   │  └─ Output/
   │     ├─ .staging/A{{no}}_{{attempt}}/   # finalize 前
   │     ├─ OutputManifest.json
   │     └─ &lt;published artifacts...&gt;
   ├─ SessionReport/                        # Run 汇总/诊断
   └─ SessionOutput/                        # 可重建对外投影
└─ SessionScratch/                          # 不得放唯一副本</pre></article>
        <article class="panel-card storage-rules-card"><h3>输入到输出的约束链</h3><ol class="storage-rules"><li><b>session 输入</b><span>原始文件不可原地覆盖；修订产生新 path/hash。</span></li><li><b>Run 输入</b><span><code>0_SessionContext/InputManifest.json</code> 冻结本次声明输入。</span></li><li><b>Step 输入</b><span>Manifest/locator 解析；Adapter staging 只是 Attempt 临时副本。</span></li><li><b>中间过程</b><span><code>Working</code> 可清，不承担业务 binding/派生摘要，不能被下游当正式产物。</span></li><li><b>正式输出</b><span>先写 <code>.staging</code>；finalize 自动生成版本 ID/hash，再原子发布到既有 <code>Output</code>。</span></li><li><b>对外目录</b><span><code>SessionOutput</code> 只做可重建投影；canonical Manifest 保持唯一。</span></li></ol><aside class="task-no-root"><b>为什么 task 没有目录？</b><p>OpenCrew 的 session 与 task 1:1 配对。task 从数据库引用 session、Run 与 Artifact ID；再复制一棵 task 文件树只会制造双份字节和清理竞态。</p></aside></article>
      </div>
      <section class="log-contract"><header><div><p class="eyebrow">Diagnostic levels</p><h3>级别描述严重性；channel 描述来源</h3></div><p><code>stdout</code>/<code>stderr</code> 是 channel，不自动等于 info/error。durable Event 不受日志阈值过滤。</p></header><div class="log-level-grid">{level_markup}</div></section>
      <div class="storage-evidence-grid">
        <article class="panel-card"><h3>当前 Run 的物理位置索引</h3><p id="storage-run-summary">—</p><div class="table-wrap storage-table"><table><thead><tr><th>Owner / purpose</th><th>Authority</th><th>Base + locator</th><th>状态</th></tr></thead><tbody id="storage-entry-body"></tbody></table></div></article>
        <aside><article class="panel-card"><h3>Demo 中真实生成的日志</h3><p>每个已执行 Step 都有 mock <code>diagnostic.ndjson</code>、<code>stdout.log</code> 与 <code>stderr.log</code>；不调用模型或外部服务。选择不同 Run 会切换路径。</p><ul class="contract-list" id="diagnostic-ref-list"></ul></article><article class="panel-card"><h3>三条不可混淆的事实</h3><ul class="storage-facts"><li>Event 在 DB；<code>events.ndjson</code> 是可搬迁证据投影。</li><li>Usage/Cost 是 ledger，不从日志文本反推。</li><li>服务日志在平台 sink，不进入 session Artifact。</li></ul></article></aside>
      </div>
      <details class="storage-index-detail"><summary>Selected Run · storage-index.json</summary><pre id="storage-index-json">—</pre></details>
    """


STORAGE_CSS = r"""
.storage-boundary{display:grid;grid-template-columns:130px 1fr;gap:18px;margin-bottom:16px;padding:16px 18px;border:1px solid color-mix(in srgb,var(--accent) 30%,var(--line));border-radius:14px;background:color-mix(in srgb,var(--accent) 5%,#fff);font-size:14px;line-height:1.7}
.storage-boundary b{color:var(--accent)}
.authority-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
.authority-plane{border:1px solid var(--line);border-radius:16px;padding:18px;background:#fff;box-shadow:0 2px 7px rgba(22,47,58,.04)}
.authority-plane small{font-size:10px;letter-spacing:.09em;color:var(--accent);font-weight:800}
.authority-plane h3{margin:8px 0;font-size:17px}
.authority-plane p{margin:0;color:var(--muted);font-size:13.5px;line-height:1.65}
.authority-plane>b{display:block;margin-top:12px;font-size:12px}
.storage-layout-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(330px,.7fr);gap:16px}
.storage-tree{margin:14px 0 0;border:1px solid var(--line);border-radius:12px;background:#f6f8f9;color:#29434e;font-size:12.5px;line-height:1.65;max-height:none;white-space:pre;overflow:auto}
.storage-tree span{color:var(--accent);font-weight:800}
.storage-rules{margin:12px 0 0;padding:0;list-style:none;counter-reset:storage}
.storage-rules li{display:grid;grid-template-columns:115px 1fr;gap:10px;padding:10px 0;border-top:1px solid var(--line);font-size:13.5px;line-height:1.55}
.storage-rules b{color:var(--text)}
.storage-rules span{color:var(--muted)}
.task-no-root{margin-top:16px;padding:14px;border-radius:12px;background:#f3f7f8;border:1px solid var(--line)}
.task-no-root p{margin:6px 0 0;color:var(--muted);font-size:13.5px;line-height:1.65}
.log-contract{margin:16px 0;padding:20px;border:1px solid var(--line);border-radius:18px;background:#fff}
.log-contract>header{display:flex;justify-content:space-between;align-items:end;gap:25px}
.log-contract h3{margin:0;font-size:20px}
.log-contract header p{margin:0;max-width:590px;color:var(--muted);font-size:13.5px;line-height:1.65}
.log-level-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin-top:16px}
.log-level{border:1px solid var(--line);border-top:3px solid #78909a;border-radius:11px;padding:12px;background:#fbfcfc}
.log-level code{font-size:12px;font-weight:850}
.log-level p{margin:7px 0 0;color:var(--muted);font-size:12.5px;line-height:1.55}
.level-info{border-top-color:#16836f}
.level-warning{border-top-color:#b77a0b}
.level-error{border-top-color:#bd4a58}
.level-critical{border-top-color:#812f48}
.storage-evidence-grid{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(310px,.5fr);gap:16px;align-items:start}
.storage-table{max-height:560px}
.storage-table table{min-width:760px}
.storage-table td{font-size:12px}
.storage-table td code{font-size:11px;overflow-wrap:anywhere}
.locator-state{display:inline-flex;padding:3px 7px;border-radius:999px;background:#eef4f5;font-size:10px;font-weight:800}
.locator-state.materialized{color:#087f6d}
.locator-state.layout_contract{color:#875a05}
.storage-facts{margin:0;padding-left:20px;color:var(--muted);font-size:13.5px;line-height:1.7}
.storage-index-detail{margin-bottom:40px}
.storage-index-detail pre{background:#f6f8f9;color:#29434e;font-size:11px}
.diagnostic-file{display:block;margin-top:3px;color:var(--muted);font-size:11px;overflow-wrap:anywhere}
.storage-rules-card code,.storage-boundary code{overflow-wrap:anywhere}
@media(max-width:1100px){.authority-grid{grid-template-columns:1fr 1fr}
.storage-layout-grid,.storage-evidence-grid{grid-template-columns:1fr}
.log-level-grid{grid-template-columns:repeat(3,1fr)}
}
@media(max-width:680px){.storage-boundary{grid-template-columns:1fr;gap:4px}
.authority-grid,.log-level-grid{grid-template-columns:1fr}
.storage-layout-grid{display:block}
.storage-tree-card,.storage-rules-card{margin-bottom:14px}
.storage-tree{font-size:10.5px;padding:12px}
.log-contract>header{display:block}
.log-contract header p{margin-top:8px}
.storage-rules li{grid-template-columns:1fr;gap:3px}
.storage-evidence-grid{display:block}
}

"""


ARTIFACT_CSS = r"""
.artifact-scope-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px}
.artifact-scope-card{min-width:0;padding:18px;border:1px solid var(--line);border-radius:15px;background:#fff;box-shadow:0 1px 4px rgba(22,47,58,.04)}
.artifact-scope-card small{color:var(--accent);font-size:10px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}
.artifact-scope-card h3{margin:8px 0;font-size:17px}
.artifact-scope-card p{margin:0;color:var(--muted);font-size:13.5px;line-height:1.68}
.artifact-scope-card code{overflow-wrap:anywhere}
.artifact-scope-card.excluded{background:#f7f9fa}
.artifact-scope-note{display:grid;grid-template-columns:150px 1fr;gap:15px;margin-bottom:16px;padding:14px 16px;border:1px solid color-mix(in srgb,var(--accent) 25%,var(--line));border-radius:13px;background:color-mix(in srgb,var(--accent) 5%,#fff)}
.artifact-scope-note b{color:var(--accent);font-size:12px}
.artifact-scope-note span{color:var(--muted);font-size:13px;line-height:1.65}
.artifact-derivation code{display:block;max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.artifact-derivation small{display:block;margin-top:4px;color:var(--muted);font-size:10.5px}
.artifact-table-hint{display:none;margin:0 0 10px;padding:9px 11px;border-radius:10px;background:#f5f8f9;color:var(--muted);font-size:12.5px;line-height:1.55}
.artifact-table-hint b{color:var(--text)}
@media(max-width:900px){.artifact-scope-grid{grid-template-columns:1fr}.artifact-scope-note{grid-template-columns:1fr;gap:5px}.artifact-table-hint{display:block}}
"""


PAGE_CSS = r"""
:root{--bg:#f3f6f8;--panel:#fff;--panel-2:#f7f9fa;--line:#d5dfe3;--text:#182d36;--muted:#526a74;--accent:#087f6d;--accent-2:#326ea8;--tint:#eef5f5;--ok:#167957;--warn:#875a05;--bad:#b33f55;--skip:#71828a;--radius:16px;color-scheme:light;font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:linear-gradient(180deg,#fbfcfd 0,#f3f6f8 460px,#f3f6f8 100%);color:var(--text);min-width:320px}
button,select{font:inherit}
.shell{width:min(1540px,calc(100% - 40px));margin:0 auto}
.topbar{height:70px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);position:relative;z-index:2}
.brand{display:flex;align-items:center;gap:12px;color:var(--text);text-decoration:none;font-weight:750;letter-spacing:-.02em}
.brand-mark{width:34px;height:34px;border-radius:11px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:grid;place-items:center;color:#fff;font-weight:950;box-shadow:0 5px 16px color-mix(in srgb,var(--accent) 18%,transparent)}
.hero{position:relative;padding:76px 0 54px;overflow:hidden}
.hero-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(320px,.75fr);gap:52px;align-items:end}
.eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);font-weight:800;margin:0 0 18px}
.hero h1{font-size:clamp(42px,6vw,82px);line-height:.98;letter-spacing:-.065em;margin:0;max-width:1050px}
.hero .subtitle{font-size:clamp(16px,1.6vw,21px);line-height:1.7;color:var(--muted);max-width:790px;margin:28px 0 0}
.hero-aside{background:#fff;border:1px solid color-mix(in srgb,var(--accent) 30%,var(--line));border-radius:28px;padding:26px;box-shadow:0 8px 24px rgba(22,47,58,.07)}
.hero-aside-top{display:flex;justify-content:space-between;gap:16px;align-items:start}
.hero-aside small{color:var(--muted);text-transform:uppercase;letter-spacing:.12em;font-size:10px}
.hero-aside strong{display:block;font-size:29px;letter-spacing:-.04em;margin-top:7px}
.pulse{width:12px;height:12px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 7px color-mix(in srgb,var(--ok) 13%,transparent);margin-top:5px}
.definition-facts{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:22px}
.definition-fact{border:1px solid var(--line);border-radius:14px;padding:13px}
.definition-fact b{display:block;font-size:15px;margin-top:4px}
.hero-network{position:absolute;right:-6%;top:18px;width:44%;height:90%;opacity:.13;pointer-events:none}
.hero-network path,.hero-network line{stroke:var(--accent);fill:none}
.hero-network circle{fill:var(--accent-2)}
.runbar-wrap{position:sticky;top:0;z-index:30;backdrop-filter:blur(20px);background:rgba(249,251,252,.94);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.runbar{min-height:76px;display:flex;gap:20px;align-items:center;justify-content:space-between}
.run-picker{display:flex;gap:8px;align-items:center;overflow:auto;padding:12px 0}
.run-picker-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.12em;margin-right:5px;white-space:nowrap}
.run-button{border:1px solid var(--line);color:var(--text);background:#fff;border-radius:13px;padding:9px 13px;text-align:left;cursor:pointer;white-space:nowrap;transition:.2s}
.run-button:hover{border-color:color-mix(in srgb,var(--accent) 50%,var(--line));color:var(--text);background:#f7fafb}
.run-button.active{background:color-mix(in srgb,var(--accent) 10%,#fff);border-color:color-mix(in srgb,var(--accent) 70%,transparent);color:var(--text)}
.run-button small{display:block;font-size:9px;color:var(--muted);margin-bottom:2px}
.current-run-state{display:flex;align-items:center;gap:10px;white-space:nowrap;font-size:13px}
.state-dot{width:9px;height:9px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 5px color-mix(in srgb,var(--ok) 12%,transparent)}
.tabs{display:flex;gap:8px;padding:30px 0 18px;overflow:auto}
.tab-button{border:1px solid transparent;background:transparent;color:var(--muted);padding:10px 15px;border-radius:12px;cursor:pointer;font-weight:700;white-space:nowrap}
.tab-button:hover{color:var(--text);background:#eef3f5}
.tab-button.active{color:var(--text);background:#fff;border-color:var(--line)}
.tab-panel{display:none}
.tab-panel.active{display:block}
.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:4px 0 24px}
.metric-card{border:1px solid var(--line);background:#fff;padding:19px 21px;border-radius:18px}
.metric-card small{display:block;color:var(--muted);font-size:10px;letter-spacing:.11em;text-transform:uppercase}
.metric-card strong{font-size:25px;letter-spacing:-.04em;display:block;margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.metric-card em{display:block;color:var(--muted);font-size:11px;font-style:normal;margin-top:5px}
.section-head{display:flex;align-items:end;justify-content:space-between;gap:30px;margin:44px 0 18px}
.section-head h2{font-size:clamp(25px,3vw,40px);letter-spacing:-.045em;margin:0}
.section-head p{color:var(--muted);max-width:680px;line-height:1.65;margin:0}
.lineage{display:flex;align-items:center;gap:8px;overflow:auto;padding:4px 0 14px}
.lineage-node{min-width:210px;border:1px solid var(--line);background:var(--panel);border-radius:16px;padding:14px;cursor:pointer}
.lineage-node.active{border-color:var(--accent);box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 30%,transparent) inset}
.lineage-node small{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.1em}
.lineage-node strong{display:block;margin:5px 0;font-size:13px}
.lineage-node span{font-size:11px;color:var(--accent)}
.lineage-arrow{color:var(--muted);font-size:18px}
.flow-layout{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:18px;align-items:start}
.flow-board{border:1px solid var(--line);border-radius:24px;background:#f8fafb;padding:18px;overflow:auto;min-height:640px}
.stage-grid{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(250px,1fr);gap:14px;min-width:max-content}
.stage-column{width:270px;position:relative}
.stage-head{display:flex;gap:10px;align-items:center;border-bottom:1px solid var(--line);padding:5px 5px 15px;margin-bottom:14px}
.stage-head>span{font-variant-numeric:tabular-nums;font-size:12px;color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);padding:6px;border-radius:9px}
.stage-head small{display:block;color:var(--muted);font-size:8px;letter-spacing:.13em}
.stage-head h3{font-size:14px;margin:1px 0 0}
.stage-line{position:absolute;left:-8px;right:-8px;top:56px;border-top:1px dashed color-mix(in srgb,var(--accent) 28%,transparent);z-index:0}
.stage-cards{display:grid;gap:10px;position:relative;z-index:1}
.step-card{width:100%;text-align:left;position:relative;border:1px solid var(--line);border-radius:17px;padding:15px;background:#fff;color:var(--text);cursor:pointer;transition:.2s;box-shadow:0 2px 7px rgba(22,47,58,.055)}
.step-card:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--accent) 50%,var(--line))}
.step-card.selected{border-color:var(--accent);box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 25%,transparent),0 18px 40px rgba(0,0,0,.3)}
.step-card.completed{background:color-mix(in srgb,var(--ok) 5%,#fff)}
.step-card.skipped{opacity:.58}
.step-card.failed{border-color:var(--bad)}
.status-orb{position:absolute;right:13px;top:14px;width:8px;height:8px;border-radius:50%;background:#53616a;box-shadow:0 0 0 4px rgba(255,255,255,.04)}
.completed .status-orb{background:var(--ok)}
.skipped .status-orb{background:var(--skip)}
.failed .status-orb{background:var(--bad)}
.waiting .status-orb{background:var(--warn)}
.step-card-head{display:flex;align-items:center;gap:7px;padding-right:16px}
.step-seq{font-size:9px;color:var(--accent);font-weight:800;letter-spacing:.02em}
.step-type{font-size:8px;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:2px 5px}
.step-card strong{display:block;font-size:15px;letter-spacing:-.015em;margin:9px 0 5px}
.step-desc{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:10px;line-height:1.5;color:var(--muted);min-height:30px}
.guard-line{font-size:9px;color:#674ea3;background:#f4f0fb;border-radius:7px;padding:5px 7px;margin-top:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.guard-line span{font-weight:800}
.badge-row{display:flex;gap:4px;flex-wrap:wrap;margin-top:10px}
.contract-badge{font-size:7px;letter-spacing:.045em;text-transform:uppercase;border:1px solid var(--line);border-radius:999px;padding:3px 5px;color:#aeb8bd}
.badge-ai{color:#087181;border-color:#bcdde2}
.badge-human{color:#a63d70;border-color:#e5c5d4}
.badge-fanout{color:#674ea3;border-color:#d5cce8}
.step-runtime{border-top:1px solid var(--line);margin-top:11px;padding-top:9px;display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:9px}
.runtime-status{font-weight:800}
.runtime-facts{color:var(--muted);text-align:right}
.detail-panel{border:1px solid var(--line);border-radius:24px;background:#fff;position:sticky;top:94px;min-height:520px;overflow:hidden}
.detail-accent{height:5px;background:linear-gradient(90deg,var(--accent),var(--accent-2))}
.detail-inner{padding:22px}
.detail-kicker{color:var(--accent);font-size:9px;text-transform:uppercase;letter-spacing:.14em;font-weight:800}
.detail-title{font-size:24px;letter-spacing:-.035em;margin:7px 0}
.detail-description{font-size:12px;line-height:1.65;color:var(--muted)}
.detail-tool-contract{display:flex;align-items:baseline;gap:7px;margin:-3px 0 12px;color:var(--muted);font-size:10px}
.detail-tool-contract span{font-weight:800;text-transform:uppercase;letter-spacing:.06em}
.detail-tool-contract code{color:#314d58;overflow-wrap:anywhere}
.detail-status{display:flex;gap:7px;align-items:center;margin:15px 0;padding:10px 12px;border-radius:12px;background:#f3f6f8;font-size:11px}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
.detail-cell{border:1px solid var(--line);padding:10px;border-radius:11px;min-width:0}
.detail-cell small{font-size:8px;color:var(--muted);text-transform:uppercase;display:block}
.detail-cell b{font-size:11px;display:block;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.contract-list{margin:15px 0 0;padding:0;list-style:none}
.contract-list li{display:grid;grid-template-columns:65px 1fr;gap:8px;border-top:1px solid var(--line);padding:9px 0;font-size:10px}
.contract-list span{color:var(--muted)}
.contract-list code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#29434e;overflow-wrap:anywhere}
.mini-items{display:flex;flex-wrap:wrap;gap:5px;margin-top:12px}
.mini-item{font-size:8px;padding:5px 7px;border-radius:8px;background:#f3f6f8;border:1px solid var(--line)}
.outcome-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin-bottom:36px}
.outcome-card{border:1px solid var(--line);background:#fff;border-radius:15px;padding:14px;display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:center;opacity:.52}
.outcome-card.active{opacity:1;border-color:color-mix(in srgb,var(--ok) 55%,var(--line));background:color-mix(in srgb,var(--ok) 8%,transparent)}
.outcome-check{width:25px;height:25px;border-radius:50%;display:grid;place-items:center;background:#edf2f4;color:var(--muted)}
.outcome-card.active .outcome-check{background:var(--ok);color:#fff}
.outcome-card strong{font-size:12px;display:block}
.outcome-card small{font-size:8px;color:var(--muted)}
.outcome-artifact{grid-column:2;font-size:8px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.panel-card{border:1px solid var(--line);background:#fff;border-radius:22px;padding:20px;margin-bottom:16px}
.panel-card h3{font-size:17px;margin:0 0 7px}
.panel-card>p{color:var(--muted);font-size:12px;line-height:1.6;margin:0 0 16px}
.trace-layout,.governance-grid,.spec-grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(300px,.6fr);gap:16px}
.filter-row{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}
.filter-button{border:1px solid var(--line);background:transparent;color:var(--muted);font-size:10px;padding:6px 9px;border-radius:8px;cursor:pointer}
.filter-button.active{color:var(--text);border-color:var(--accent);background:color-mix(in srgb,var(--accent) 9%,#fff)}
.event-list{display:grid;gap:0;max-height:760px;overflow:auto}
.event-row{display:grid;grid-template-columns:75px 13px minmax(120px,210px) 1fr;gap:10px;align-items:start;border-top:1px solid var(--line);padding:12px 3px;font-size:10px}
.event-time{color:var(--muted);font-variant-numeric:tabular-nums}
.event-dot{width:8px;height:8px;border-radius:50%;background:var(--accent);margin-top:3px}
.event-kind{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#29434e}
.event-payload{color:var(--muted);word-break:break-word}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:14px}
table{border-collapse:collapse;width:100%;font-size:10px;min-width:780px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{position:sticky;top:0;background:#edf2f4;color:var(--muted);font-size:8px;text-transform:uppercase;letter-spacing:.08em}
td code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#29434e}
.validity{color:var(--ok);font-weight:800}
.budget-hero{padding:23px;border-radius:18px;background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 16%,transparent),color-mix(in srgb,var(--accent-2) 8%,transparent));border:1px solid color-mix(in srgb,var(--accent) 28%,var(--line));margin-bottom:16px}
.budget-top{display:flex;align-items:end;justify-content:space-between;gap:20px}
.budget-top small{color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.1em}
.budget-top strong{font-size:34px;display:block;letter-spacing:-.05em;margin-top:5px}
.budget-track{height:9px;background:rgba(255,255,255,.08);border-radius:99px;overflow:hidden;margin:18px 0 7px}
.budget-fill{height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--accent-2));border-radius:99px;transition:width .4s}
.budget-caption{display:flex;justify-content:space-between;color:var(--muted);font-size:9px}
.profile-list{display:grid;gap:8px}
.profile-card{border:1px solid var(--line);border-radius:14px;padding:13px;background:#fff}
.profile-card strong{font-size:12px}
.profile-card small{display:block;color:var(--muted);font-size:9px;margin-top:3px}
.profile-pills{display:flex;gap:5px;flex-wrap:wrap;margin-top:8px}
.profile-pills span{font-size:8px;padding:4px 6px;border:1px solid var(--line);border-radius:999px}
.principle-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.principle{border:1px solid var(--line);border-radius:16px;padding:16px;background:#fff}
.principle span{font-size:9px;color:var(--accent);font-weight:800}
.principle strong{display:block;margin:8px 0 5px;font-size:13px}
.principle p{color:var(--muted);font-size:10px;line-height:1.55;margin:0}
.mapping-list{display:grid;gap:7px}
.mapping-row{display:grid;grid-template-columns:150px 1fr auto;gap:12px;align-items:center;border:1px solid var(--line);border-radius:12px;padding:11px}
.mapping-row strong{font-size:11px}
.mapping-row span{font-size:10px;color:var(--muted)}
.mapping-row code{font-size:8px;color:var(--accent);font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.digest-list{display:grid;gap:8px}
.digest-row{border:1px solid var(--line);border-radius:11px;padding:10px}
.digest-row small{display:block;color:var(--muted);font-size:8px}
.digest-row code{display:block;font-size:8px;color:var(--muted);margin-top:5px;word-break:break-all}
details{border:1px solid var(--line);border-radius:14px;margin-top:10px;overflow:hidden}
summary{cursor:pointer;padding:13px 15px;font-size:11px;font-weight:750;background:#f3f6f8}
pre{margin:0;padding:16px;overflow:auto;max-height:540px;background:#f6f8f9;color:#29434e;font-size:9px;line-height:1.55;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.footer{border-top:1px solid var(--line);margin-top:70px;padding:30px 0 50px;color:var(--muted);font-size:11px;display:flex;justify-content:space-between;gap:20px}
.noscript{margin:20px auto;padding:14px;border:1px solid var(--bad);border-radius:12px;color:#8c3043;background:#fff}
@media(max-width:1100px){.hero-grid,.flow-layout,.trace-layout,.governance-grid,.spec-grid{grid-template-columns:1fr}
.hero-aside{max-width:650px}
.detail-panel{position:relative;top:auto;min-height:0}
.metric-grid{grid-template-columns:1fr 1fr}
.principle-grid{grid-template-columns:1fr 1fr}
}
@media(max-width:680px){.shell{width:min(100% - 22px,1540px)}
.hero{padding:50px 0 36px}
.hero-grid{gap:28px}
.hero h1{font-size:43px}
.definition-facts{grid-template-columns:1fr 1fr}
.runbar{align-items:flex-start;flex-direction:column;gap:0;padding-bottom:10px}
.current-run-state{padding-bottom:4px}
.metric-grid{grid-template-columns:1fr}
.section-head{display:block}
.section-head p{margin-top:9px}
.principle-grid{grid-template-columns:1fr}
.mapping-row{grid-template-columns:1fr}
.event-row{grid-template-columns:65px 10px 1fr}
.event-payload{grid-column:3}
.footer{display:block}
.footer span{display:block;margin-top:8px}
}
@media print{.runbar-wrap,.tabs,.detail-panel,.filter-row{display:none!important}
.tab-panel{display:block!important}
.flow-layout,.trace-layout,.governance-grid,.spec-grid{grid-template-columns:1fr}
.flow-board{overflow:visible}
.stage-grid{display:grid;grid-template-columns:repeat(3,1fr);min-width:0}
.stage-column{width:auto}
.panel-card{break-inside:avoid}
body{background:#fff;color:#111}
.step-card,.panel-card,.metric-card,.flow-board{color:#111;background:#fff;border-color:#ccc}
.step-desc,.panel-card>p,.section-head p{color:#555}
}
"""


EXPLAINER_CSS = r"""
.scenario-guide{margin:20px 0 8px;border:1px solid color-mix(in srgb,var(--accent) 24%,var(--line));border-radius:24px;padding:24px;background:#fff}
.guide-lead{display:grid;grid-template-columns:minmax(280px,.8fr) minmax(0,1.2fr);gap:36px;align-items:end}
.guide-lead .eyebrow{margin-bottom:10px}
.guide-lead h2{font-size:clamp(25px,3vw,39px);line-height:1.08;letter-spacing:-.045em;margin:0}
.guide-lead>p{font-size:13px;line-height:1.75;color:var(--muted);margin:0}
.guide-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:22px}
.guide-card{position:relative;border:1px solid var(--line);border-radius:17px;padding:17px;background:#fff;overflow:hidden}
.guide-number{position:absolute;right:14px;top:11px;color:color-mix(in srgb,var(--accent) 45%,transparent);font-size:23px;font-weight:900;letter-spacing:-.05em}
.guide-card h3{font-size:15px;line-height:1.3;margin:0 38px 15px 0;min-height:39px}
.guide-rule{border-top:1px solid var(--line);padding-top:10px;margin-top:9px}
.guide-rule small{display:block;color:var(--ok);font-size:8px;text-transform:uppercase;letter-spacing:.12em;font-weight:800}
.guide-rule.constraint small{color:var(--accent-2)}
.guide-rule p{font-size:10px;line-height:1.6;color:var(--muted);margin:5px 0 0}
.guide-effect{display:grid;grid-template-columns:190px 1fr;gap:18px;align-items:start;margin-top:12px;border-radius:14px;padding:14px 16px;background:color-mix(in srgb,var(--accent) 7%,#fff);border:1px solid color-mix(in srgb,var(--accent) 18%,var(--line))}
.guide-effect small{color:var(--accent);font-size:9px;text-transform:uppercase;letter-spacing:.1em;font-weight:850}
.guide-effect p{font-size:11px;line-height:1.65;color:var(--muted);margin:0}
.audience-paths{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
.audience-paths span{border:1px solid var(--line);border-radius:11px;padding:10px 12px;color:var(--muted);font-size:9px;line-height:1.55}
.audience-paths b{color:var(--text);margin-right:5px}
.glossary-block{display:grid;grid-template-columns:230px 1fr;gap:20px;margin-top:20px;padding-top:18px;border-top:1px solid var(--line)}
.glossary-block header small{color:var(--accent);font-size:8px;text-transform:uppercase;letter-spacing:.12em;font-weight:800}
.glossary-block h3{font-size:15px;margin:6px 0 0}
.glossary-block dl{display:grid;grid-template-columns:repeat(2,1fr);gap:7px;margin:0}
.glossary-item{border:1px solid var(--line);border-radius:11px;padding:10px 11px;background:#f8fafb}
.glossary-item dt{font-size:10px;color:var(--text);font-weight:800}
.glossary-item dd{font-size:9px;line-height:1.55;color:var(--muted);margin:4px 0 0}
@media(max-width:1100px){.guide-grid{grid-template-columns:1fr 1fr}
.guide-card:last-child{grid-column:1/-1}
.glossary-block{grid-template-columns:1fr}
.glossary-block dl{grid-template-columns:repeat(2,1fr)}
}
@media(max-width:680px){.scenario-guide{padding:18px}
.guide-lead,.guide-grid,.guide-effect,.audience-paths,.glossary-block,.glossary-block dl{grid-template-columns:1fr}
.guide-card:last-child{grid-column:auto}
.guide-card h3{min-height:0}
.guide-effect{gap:7px}
}

"""


READABILITY_CSS = r"""
/* Browser-verified readability and responsive-flow overrides. */
.guide-lead>p{font-size:14px}
.guide-rule small{font-size:9px}
.guide-rule p{font-size:12px;line-height:1.68;color:var(--muted)}
.guide-effect small{font-size:10px}
.guide-effect p{font-size:12px}
.audience-paths span{font-size:11px;line-height:1.6}
.glossary-block header small{font-size:9px}
.glossary-block h3{font-size:17px}
.glossary-item dt{font-size:12px}
.glossary-item dd{font-size:11px;line-height:1.6;color:var(--muted)}
.step-desc{font-size:11px;line-height:1.55}
.current-run-state{min-width:0}
.current-run-state strong{min-width:0}
.hero-grid>*,.hero-aside,.definition-fact{min-width:0}
.definition-fact b{overflow-wrap:anywhere}
.hero h1,.hero .subtitle{max-width:100%;overflow-wrap:anywhere}
.flow-layout,.scenario-guide{scroll-margin-top:124px}
.flow-board{scrollbar-color:color-mix(in srgb,var(--accent) 55%,transparent) #e8eef0;scrollbar-width:thin}
.flow-board::-webkit-scrollbar{height:9px}
.flow-board::-webkit-scrollbar-track{background:#e8eef0;border-radius:999px}
.flow-board::-webkit-scrollbar-thumb{background:color-mix(in srgb,var(--accent) 55%,transparent);border-radius:999px}
.flow-scroll-hint{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 2px 14px;padding:8px 10px;border:1px dashed color-mix(in srgb,var(--accent) 30%,var(--line));border-radius:10px;color:var(--muted);font-size:10px;line-height:1.45}
.flow-scroll-hint b{color:var(--accent)}
.mobile-flow-hint{display:none}
.detail-status{min-width:0;flex-wrap:wrap}
.event-kind{overflow-wrap:anywhere}
.executive-tldr{margin:32px auto 0;padding:16px 18px;display:grid;grid-template-columns:132px minmax(0,1fr);gap:18px;align-items:stretch;border:1px solid color-mix(in srgb,var(--accent) 32%,var(--line));border-radius:18px;background:#fff;box-shadow:0 2px 9px rgba(22,47,58,.04)}
.tldr-kicker{display:flex;align-items:center;color:var(--accent);font-size:10px;font-weight:850;letter-spacing:.12em;text-transform:uppercase}
.executive-tldr p{display:grid;grid-template-columns:.85fr 1.15fr 1.25fr;margin:0}
.tldr-clause{padding:2px 16px;border-left:1px solid var(--line);color:var(--muted);font-size:12px;line-height:1.62}
.tldr-clause b{display:block;margin-bottom:3px;color:var(--text);font-size:10px;letter-spacing:.08em}
.tldr-boundary{color:var(--muted);background:color-mix(in srgb,var(--bad) 5%,#fff)}
.tldr-boundary b{color:var(--warn)}
.agent-ref{display:block;margin-top:5px;color:var(--accent);font-size:9px;line-height:1.45;overflow-wrap:anywhere}
.agent-limits{color:#78408a;border-color:#d9c4df!important}

@media(max-width:900px){
  .executive-tldr{grid-template-columns:1fr}
  .tldr-kicker{padding-bottom:2px}
}

@media(max-width:680px){
  .run-picker,.current-run-state{width:100%;max-width:100%;min-width:0}
  .current-run-state strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .guide-lead>p{font-size:13px}
  .guide-card{padding:16px}
  .guide-card h3{font-size:16px}
  .guide-rule p,.guide-effect p{font-size:12px}
  .audience-paths span,.glossary-item dd{font-size:11px}
  .flow-board{overflow:visible;min-height:0;padding:13px}
  .stage-grid{grid-auto-flow:row;grid-auto-columns:auto;grid-template-columns:minmax(0,1fr);min-width:0;gap:18px}
  .stage-column{width:auto;min-width:0}
  .stage-line{display:none}
  .desktop-flow-hint{display:none}
  .mobile-flow-hint{display:inline}
  .executive-tldr{margin-top:26px;padding:14px;gap:10px}
  .executive-tldr p{grid-template-columns:1fr}
  .tldr-clause{padding:10px 2px;border-left:0;border-top:1px solid var(--line)}
}
"""


GRAPH_CONTROL_CSS = r"""
.step-order{padding:3px 5px;border-radius:6px;background:color-mix(in srgb,var(--accent) 10%,transparent);color:var(--accent);font-size:8px;font-weight:900;letter-spacing:.07em}
.dependency-map{margin:0 0 18px;padding:20px;border:1px solid color-mix(in srgb,var(--accent) 24%,var(--line));border-radius:23px;background:#fff}
.dependency-map-head{display:grid;grid-template-columns:minmax(280px,.8fr) minmax(300px,1.2fr);gap:30px;align-items:end}
.dependency-map-head .eyebrow{margin-bottom:8px}
.dependency-map-head h3{margin:0;font-size:24px;letter-spacing:-.035em}
.dependency-map-head>p{margin:0;color:var(--muted);font-size:12px;line-height:1.65}
.dependency-map-head b{color:var(--text)}
.dag-legend{display:flex;flex-wrap:wrap;gap:7px;margin:16px 0 10px}
.dag-legend span{display:flex;align-items:center;gap:6px;padding:5px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:8px}
.legend-line{display:inline-block;width:20px;border-top:2px solid var(--accent)}
.legend-line.guarded{border-top-color:var(--accent-2);border-top-style:dashed}
.dag-map-scroll{overflow:auto;max-width:100%;border:1px solid var(--line);border-radius:16px;background:#f7f9fa;scrollbar-color:color-mix(in srgb,var(--accent) 58%,transparent) #e8eef0;scrollbar-width:thin}
.dag-map-scroll:focus{outline:2px solid color-mix(in srgb,var(--accent) 55%,transparent);outline-offset:2px}
.dag-map-canvas{position:relative;min-height:260px;padding:20px}
.dag-map-layers{position:relative;z-index:2;display:grid;grid-auto-flow:column;grid-auto-columns:235px;gap:72px;align-items:start}
.dag-map-layer{display:grid;gap:13px;align-content:start}
.dag-map-layer>header{padding:0 3px 7px;border-bottom:1px solid var(--line);color:#70838c;font-size:8px;letter-spacing:.13em}
.dag-map-node{position:relative;min-height:114px;padding:13px;border:1px solid var(--line);border-radius:14px;background:#fff;color:var(--text);text-align:left;cursor:pointer;box-shadow:0 2px 7px rgba(22,47,58,.055);transition:.18s}
.dag-map-node:hover,.dag-map-node.is-selected{border-color:var(--accent);transform:translateY(-1px);box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 23%,transparent),0 12px 28px rgba(22,47,58,.08)}
.dag-node-top{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:7px;align-items:center}
.dag-order{display:grid;place-items:center;width:24px;height:24px;border-radius:8px;background:color-mix(in srgb,var(--accent) 13%,transparent);color:var(--accent);font-size:9px;font-weight:900}
.dag-node-top code{overflow:hidden;color:var(--muted);font-size:8px;text-overflow:ellipsis;white-space:nowrap}
.dag-node-state{font-size:8px;color:#82929a}
.dag-map-node>strong{display:block;margin:11px 0 4px;font-size:12px;letter-spacing:-.01em}
.dag-map-node>small{display:block;color:var(--muted);font-size:8px;text-transform:uppercase;letter-spacing:.08em}
.dag-map-node>small code{font:inherit;letter-spacing:0;text-transform:none}
.dag-role-row{display:flex;flex-wrap:wrap;gap:4px;margin-top:9px}
.dag-role{padding:3px 5px;border:1px solid var(--line);border-radius:999px;font-size:7px}
.dag-role.fork{color:#08705f;border-color:#bdded5}
.dag-role.join{color:#245a9a;border-color:#c3d5e7}
.dag-role.guard{color:#984268;border-color:#e2c7d3}
.dag-map-node.is-completed .dag-node-state{color:var(--ok)}
.dag-map-node.is-skipped{opacity:.58}
.dag-map-node.is-failed{border-color:var(--bad)}
.dag-edge-layer{position:absolute;z-index:1;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none}
.dag-edge{fill:none;stroke:color-mix(in srgb,var(--accent) 72%,#8ca0a8);stroke-width:2;opacity:.8}
.dag-edge.is-guarded{stroke:var(--accent-2);stroke-dasharray:6 5}
.dag-edge.is-any{stroke:#c4b5fd;stroke-dasharray:3 4}
.dependency-mobile-list{display:none}
.run-control-panel{margin-bottom:18px;padding:22px;border:1px solid color-mix(in srgb,var(--accent-2) 26%,var(--line));border-radius:23px;background:#fff}
.run-control-head{display:grid;grid-template-columns:minmax(300px,.9fr) minmax(320px,1.1fr);gap:32px;align-items:end}
.run-control-head .eyebrow{margin-bottom:8px}
.run-control-head h2{margin:0;font-size:27px;letter-spacing:-.04em}
.run-control-head>div:last-child>span{display:inline-flex;padding:5px 8px;border:1px solid rgba(246,200,95,.28);border-radius:999px;color:var(--warn);font-size:8px;font-weight:850;letter-spacing:.08em}
.run-control-head>div:last-child>p{margin:8px 0 0;color:var(--muted);font-size:11px;line-height:1.65}
.run-control-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:18px}
.run-control-card{min-height:190px;padding:15px;border:1px solid var(--line);border-radius:15px;background:#f8fafb}
.run-control-card header{display:flex;align-items:center;justify-content:space-between;gap:8px}
.run-control-card header>span{color:var(--accent);font-size:9px;font-weight:900}
.run-control-card header code{color:var(--accent-2);font-size:8px}
.run-control-card h3{margin:24px 0 8px;font-size:14px}
.run-control-card p{margin:0;color:var(--muted);font-size:11px;line-height:1.62}
.run-control-card>small{display:block;margin-top:13px;padding-top:10px;border-top:1px solid var(--line);color:var(--muted);font-size:8px;line-height:1.55}
.run-control-note{display:grid;grid-template-columns:120px 1fr;gap:15px;margin-top:10px;padding:13px 15px;border:1px solid rgba(246,200,95,.22);border-radius:13px;background:rgba(246,200,95,.035)}
.run-control-note b{color:var(--warn);font-size:9px}
.run-control-note span{color:var(--muted);font-size:10px;line-height:1.6}

@media(max-width:1100px){
  .run-control-grid{grid-template-columns:1fr 1fr}
  .run-control-card{min-height:0}
}

@media(max-width:680px){
  .dependency-map{padding:15px}
  .dependency-map-head,.run-control-head{grid-template-columns:1fr;gap:10px}
  .dependency-map-head h3{font-size:22px}
  .dag-map-scroll{display:none}
  .dependency-mobile-list{display:grid;gap:7px;margin-top:12px}
  .dependency-mobile-row{display:grid;grid-template-columns:31px minmax(0,1fr) auto;gap:9px;align-items:start;padding:11px;border:1px solid var(--line);border-radius:12px;background:#fff;color:var(--text);text-align:left;cursor:pointer}
  .dependency-mobile-row>span{display:grid;place-items:center;width:28px;height:28px;border-radius:8px;background:color-mix(in srgb,var(--accent) 12%,transparent);color:var(--accent);font-size:9px;font-weight:900}
  .dependency-mobile-row strong{display:block;font-size:11px;overflow-wrap:anywhere}
  .dependency-mobile-row code{display:block;margin-top:3px;color:#6b7f88;font-size:8px;overflow-wrap:anywhere}
  .dependency-mobile-row small{display:block;margin-top:6px;color:var(--muted);font-size:10px;line-height:1.55}
  .dependency-mobile-row small b{display:block;color:var(--accent-2);font-size:8px;letter-spacing:.05em}
  .dependency-mobile-row em{padding:3px 5px;border:1px solid var(--line);border-radius:999px;color:#7f929a;font-size:7px;font-style:normal}
  .run-control-panel{padding:16px}
  .run-control-head h2{font-size:25px}
  .run-control-grid{grid-template-columns:1fr}
  .run-control-card{min-height:0}
  .run-control-card h3{margin-top:16px}
  .run-control-note{grid-template-columns:1fr;gap:5px}
}

@media print{
  .dag-map-scroll{overflow:visible}
  .run-control-grid{grid-template-columns:1fr 1fr}
}
"""


DOCUMENT_THEME_CSS = r"""
body{
  font-size:15px;
  line-height:1.6;
}
.shell{width:min(1420px,calc(100% - 48px))}
.topbar{height:64px}
.brand-mark{color:#fff;box-shadow:0 5px 16px color-mix(in srgb,var(--accent) 18%,transparent)}
.hero{padding:54px 0 42px}
.hero-grid{grid-template-columns:minmax(0,1.18fr) minmax(320px,.72fr);gap:46px;align-items:center}
.hero h1{max-width:940px;font-size:clamp(38px,4vw,54px);line-height:1.08;letter-spacing:-.04em}
.hero .subtitle{max-width:820px;margin-top:22px;color:var(--muted);font-size:clamp(17px,1.45vw,19px);line-height:1.75}
.hero-network{opacity:.055}
.hero-aside{padding:23px;border-radius:18px;background:#fff;box-shadow:0 8px 24px rgba(22,47,58,.07)}
.hero-aside small{font-size:11px}
.hero-aside strong{font-size:27px}
.definition-fact{background:#f8fafb}
.definition-fact b{font-size:15px}
.executive-tldr{border-radius:16px;background:#fff;box-shadow:0 2px 9px rgba(22,47,58,.04)}
.tldr-kicker{font-size:11px}
.tldr-clause{color:var(--muted);font-size:14px;line-height:1.68}
.tldr-clause b{color:var(--text);font-size:11px}
.runbar-wrap{background:rgba(249,251,252,.94);box-shadow:0 2px 10px rgba(22,47,58,.05)}
.run-button{background:#fff;color:var(--text)}
.run-button:hover{color:var(--text);background:#f7fafb}
.run-button.active{color:var(--text);background:color-mix(in srgb,var(--accent) 10%,#fff)}
.run-button small,.run-picker-label{font-size:10.5px}
.tabs{padding-top:24px}
.tab-button:hover{color:var(--text);background:#eef3f5}
.tab-button.active{color:var(--text);background:#fff;border-color:var(--line);box-shadow:0 1px 3px rgba(22,47,58,.06)}
.metric-card,.panel-card,.principle,.profile-card,.digest-row,.mapping-row,details{background:#fff;box-shadow:0 1px 3px rgba(22,47,58,.035)}
.metric-card small{font-size:11px}
.metric-card em{font-size:12px}
.section-head{margin-top:38px}
.section-head h2{font-size:clamp(27px,2.7vw,37px);letter-spacing:-.035em}
.section-head p{font-size:14px;line-height:1.72}
.lineage-node{background:#fff}
.lineage-node small{font-size:10px}
.lineage-node strong{font-size:13px}
.flow-board{min-height:620px;border-radius:17px;background:#f8fafb}
.flow-scroll-hint{color:var(--muted);font-size:12px;background:#fff}
.stage-head small{font-size:9.5px}
.stage-head h3{font-size:15px}
.step-card{border-radius:13px;background:#fff;box-shadow:0 2px 7px rgba(22,47,58,.055)}
.step-card.completed{background:color-mix(in srgb,var(--ok) 5%,#fff)}
.step-card.selected{box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 24%,transparent),0 5px 15px rgba(22,47,58,.08)}
.step-seq{font-size:10px}
.step-type{font-size:9px}
.step-card strong{font-size:15px}
.step-desc{min-height:40px;color:var(--muted);font-size:13px;line-height:1.55}
.guard-line{color:#66469b;background:#f2eef9;font-size:10.5px}
.contract-badge{color:#536b75;font-size:8.5px}
.badge-ai{color:#087181}.badge-human{color:#a63d70}.badge-fanout{color:#674ea3}
.step-runtime{font-size:10.5px}
.detail-panel{border-radius:17px;background:#fff;box-shadow:0 2px 8px rgba(22,47,58,.055)}
.detail-kicker{font-size:10px}
.detail-title{font-size:23px}
.detail-description{color:var(--muted);font-size:13.5px;line-height:1.7}
.detail-status{background:#f3f6f8;font-size:12px}
.detail-cell{background:#f9fafb}
.detail-cell small{font-size:9.5px}
.detail-cell b{font-size:11.5px}
.contract-list li{font-size:11.5px}
.contract-list code,.event-kind,td code{color:#314d58}
.mini-item{background:#f3f6f8;font-size:9.5px}
.outcome-card{background:#fff}
.outcome-card strong{font-size:13px}
.outcome-card small,.outcome-artifact{font-size:9.5px}
.filter-button{font-size:11px}
.filter-button.active{color:var(--text);background:color-mix(in srgb,var(--accent) 9%,#fff)}
.event-row{font-size:11.5px;line-height:1.55}
table{font-size:11.5px}
th{background:#f1f5f6;font-size:10px}
.panel-card>p{font-size:14px;line-height:1.68}
.budget-top small{font-size:10px}
.budget-track{background:#dfe7ea}
.budget-caption{font-size:10px}
.profile-card small{font-size:10.5px}
.profile-pills span{font-size:9.5px}
.principle span{font-size:10px}
.principle strong{font-size:14px}
.principle p{font-size:12.5px;line-height:1.65}
.mapping-row strong{font-size:12px}
.mapping-row span{font-size:12px}
.mapping-row code{font-size:9.5px}
.digest-row small{font-size:9.5px}
.digest-row code{color:#465e68;font-size:9.5px}
summary{background:#f5f8f9;font-size:12px}
pre{background:#f4f7f8;color:#314b56;font-size:10.5px}
.footer{font-size:12px}
.scenario-guide{border-radius:17px;background:#fff;box-shadow:0 2px 8px rgba(22,47,58,.04)}
.guide-lead h2{font-size:clamp(26px,2.7vw,34px);line-height:1.16;letter-spacing:-.03em}
.guide-lead>p{color:var(--muted);font-size:14.5px;line-height:1.76}
.guide-card{border-radius:13px;background:#f8fafb}
.guide-card h3{font-size:16px}
.guide-rule small{font-size:9.5px}
.guide-rule p{color:var(--muted);font-size:13.5px;line-height:1.68}
.guide-effect{background:color-mix(in srgb,var(--accent) 7%,#fff)}
.guide-effect small{font-size:10px}
.guide-effect p{color:var(--text);font-size:13.5px;line-height:1.68}
.audience-paths span{color:var(--muted);font-size:12.5px}
.audience-paths b{color:var(--text)}
.glossary-block header small{font-size:9.5px}
.glossary-block h3{font-size:17px}
.glossary-item{background:#f8fafb}
.glossary-item dt{color:var(--text);font-size:12.5px}
.glossary-item dd{color:var(--muted);font-size:12.5px;line-height:1.65}
.agent-ref{font-size:10px}
.dependency-map{border-radius:17px;background:#fff;box-shadow:0 2px 8px rgba(22,47,58,.04)}
.dependency-map-head h3{font-size:25px}
.dependency-map-head>p{color:var(--muted);font-size:14px;line-height:1.7}
.dependency-map-head b{color:var(--text)}
.dag-legend span{color:var(--muted);font-size:9.5px;background:#f8fafb}
.dag-map-scroll{background:#f6f9fa;scrollbar-color:color-mix(in srgb,var(--accent) 55%,transparent) #e8eef0}
.dag-map-layer>header{color:#607781;font-size:9.5px}
.dag-map-node{min-height:120px;border-radius:12px;background:#fff;box-shadow:0 2px 7px rgba(22,47,58,.055)}
.dag-map-node:hover,.dag-map-node.is-selected{box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 21%,transparent),0 4px 12px rgba(22,47,58,.08)}
.dag-order{font-size:10px}
.dag-node-top code{color:#526a74;font-size:9px}
.dag-node-state{color:#667b84;font-size:9px}
.dag-map-node>strong{font-size:13px}
.dag-map-node>small{color:#607781;font-size:9px}
.dag-role{font-size:8.5px}
.dag-role.fork{color:#08705f}.dag-role.join{color:#245a9a}.dag-role.guard{color:#984268}
.dag-edge{stroke:color-mix(in srgb,var(--accent) 80%,#546c75);opacity:.9}
.dag-edge.is-any{stroke:#7255ab}
.run-control-panel{border-radius:17px;background:#fff;box-shadow:0 2px 8px rgba(22,47,58,.04)}
.run-control-head h2{font-size:25px;letter-spacing:-.03em}
.run-control-head>div:last-child>span{color:var(--warn);font-size:9.5px;background:#fffaf0}
.run-control-head>div:last-child>p{color:var(--muted);font-size:13.5px;line-height:1.68}
.run-control-card{min-height:205px;border-radius:13px;background:#f8fafb}
.run-control-card header>span{font-size:10px}
.run-control-card header code{font-size:9px}
.run-control-card h3{font-size:15px}
.run-control-card p{color:var(--muted);font-size:13.5px;line-height:1.66}
.run-control-card>small{color:#657a83;font-size:11px;line-height:1.6}
.run-control-note{background:#fffaf0}
.run-control-note b{font-size:10.5px}
.run-control-note span{color:var(--muted);font-size:12.5px;line-height:1.65}
.run-control-note code{color:#5c470e}
@media(max-width:1100px){
  .hero-grid{grid-template-columns:minmax(0,1fr);align-items:start}
}
@media(max-width:680px){
  .shell{width:min(100% - 24px,1420px)}
  .hero{padding:38px 0 30px}
  .hero h1{font-size:34px;line-height:1.1;letter-spacing:-.035em}
  .hero .subtitle{font-size:16px}
  .hero-aside{border-radius:15px}
  .section-head h2{font-size:28px}
  .scenario-guide,.dependency-map,.run-control-panel{border-radius:15px}
  .guide-lead h2{font-size:27px}
  .guide-lead>p{font-size:14px}
  .dependency-map-head h3{font-size:22px}
  .dependency-mobile-row{background:#fff}
  .dependency-mobile-row strong{font-size:12.5px}
  .dependency-mobile-row small{color:var(--muted);font-size:11.5px}
  .dependency-mobile-row small b{font-size:9px}
  .dependency-mobile-row em{color:#617781;font-size:8.5px}
  .run-control-head h2{font-size:23px}
  .run-control-card{min-height:0}
}
@media print{
  body{background:#fff}
  .metric-card,.panel-card,.scenario-guide,.dependency-map,.run-control-panel,.step-card{box-shadow:none}
}
"""


PAGE_JS = r"""
const DATA=JSON.parse(document.getElementById('demo-data').textContent);let selectedRun=DATA.runs[DATA.runs.length-1];let selectedStepId=DATA.process.steps[0].id;const money=(v,c='USD')=>new Intl.NumberFormat('zh-CN',{style:'currency',currency:c,minimumFractionDigits:3,maximumFractionDigits:3}).format(Number(v||0));const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const compact=v=>JSON.stringify(v);const statusLabel=s=>({completed:'已完成',skipped:'分支关闭',waiting:'等待中',running:'运行中',failed:'失败',blocked:'阻断',cancelled:'已取消'}[s]||s);function runStep(id){return selectedRun.steps.find(s=>s.step_id===id)}function runCostForStep(id){return selectedRun.usage_records.filter(u=>u.step_id===id).reduce((n,u)=>n+Number(u.cost?.amount||0),0)}function selectRun(id){selectedRun=DATA.runs.find(r=>r.run_id===id)||selectedRun;document.querySelectorAll('.run-button,.lineage-node').forEach(el=>el.classList.toggle('active',el.dataset.runId===selectedRun.run_id));document.getElementById('current-run-label').textContent=`${selectedRun.status} · ${selectedRun.outcome}`;renderSummary();renderLineage();renderFlow();renderTrace();renderArtifacts();renderGovernance();renderSpec();selectStep(selectedStepId)}function renderRunPicker(){const host=document.getElementById('run-picker');host.innerHTML='<span class="run-picker-label">选择运行记录</span>'+DATA.runs.map((run,i)=>`<button class="run-button" data-run-id="${esc(run.run_id)}"><small>RUN ${run.run_sequence}${run.supersedes_run_id?' · REVISION':''}</small>${esc(run.outcome)}</button>`).join('');host.querySelectorAll('button').forEach(btn=>btn.addEventListener('click',()=>selectRun(btn.dataset.runId)))}function renderSummary(){const done=selectedRun.steps.filter(s=>s.status==='completed').length;const skipped=selectedRun.steps.filter(s=>s.status==='skipped').length;const fanout=selectedRun.steps.reduce((n,s)=>n+s.fanout_items.length,0);const values=[['业务 outcome',selectedRun.outcome,`Run ${selectedRun.run_sequence} · ${selectedRun.status}`],['步骤闭合',`${done}/${selectedRun.steps.length}`,`${skipped} skipped · 0 unresolved`],['Artifact',String(selectedRun.artifacts.length),`${fanout} fanout items · all hash-bound`],['AI provider cost',money(selectedRun.budget_summary.settled,selectedRun.budget_summary.currency),`${selectedRun.usage_records.length} immutable observations`]];document.getElementById('metric-grid').innerHTML=values.map(v=>`<article class="metric-card"><small>${esc(v[0])}</small><strong title="${esc(v[1])}">${esc(v[1])}</strong><em>${esc(v[2])}</em></article>`).join('');document.querySelectorAll('.outcome-card').forEach(el=>el.classList.toggle('active',el.dataset.outcomeId===selectedRun.outcome))}function renderLineage(){const host=document.getElementById('lineage');host.innerHTML=DATA.runs.map((run,i)=>`${i?'<span class="lineage-arrow">→</span>':''}<button class="lineage-node" data-run-id="${esc(run.run_id)}"><small>${run.supersedes_run_id?'supersedes previous':'business instance opened'}</small><strong>${esc(run.run_id)}</strong><span>${esc(run.outcome)}</span></button>`).join('');host.querySelectorAll('button').forEach(btn=>btn.addEventListener('click',()=>selectRun(btn.dataset.runId)))}function renderFlow(){document.querySelectorAll('.step-card').forEach(card=>{const rec=runStep(card.dataset.stepId);card.classList.remove('completed','skipped','waiting','running','failed','blocked','cancelled');card.classList.add(rec.status);card.querySelector('.runtime-status').textContent=statusLabel(rec.status);const cost=runCostForStep(rec.step_id);const facts=[];if(rec.attempts.length)facts.push(`${rec.attempts.length} attempt`);if(rec.fanout_items.length)facts.push(`${rec.fanout_items.length} items`);if(cost)facts.push(money(cost,selectedRun.budget_summary.currency));if(rec.artifact_ids.length)facts.push(`${rec.artifact_ids.length} art.`);card.querySelector('.runtime-facts').textContent=facts.join(' · ')||'guard only'})}function selectStep(id){selectedStepId=id;document.querySelectorAll('.step-card').forEach(card=>card.classList.toggle('selected',card.dataset.stepId===id));const step=DATA.process.steps.find(s=>s.id===id);const rec=runStep(id);const artifacts=selectedRun.artifacts.filter(a=>a.producer.step_id===id);const usages=selectedRun.usage_records.filter(u=>u.step_id===id);const human=selectedRun.human_tasks.find(w=>w.step_id===id);const itemPills=rec.fanout_items.map(item=>`<span class="mini-item">${esc(item.item_key)} · ${esc(item.status)}</span>`).join('');const skip=rec.skip_reason?`<div class="detail-status">↳ ${esc(rec.skip_reason.kind)} · ${esc(rec.skip_reason.detail)}</div>`:'';document.getElementById('detail-inner').innerHTML=`<span class="detail-kicker">${esc(step.id)} · ${esc(step.stage)}</span><h3 class="detail-title">${esc(step.tool)}</h3><p class="detail-description">${esc(step.description||'')}</p><div class="detail-status"><span class="status-dot"></span><strong>${statusLabel(rec.status)}</strong><span>· ${rec.attempts.length} attempt · ${rec.fanout_items.length} fanout item</span></div>${skip}<div class="detail-grid"><div class="detail-cell"><small>side effect</small><b>${esc(step.side_effect_class)}</b></div><div class="detail-cell"><small>provider cost</small><b>${money(runCostForStep(id),selectedRun.budget_summary.currency)}</b></div><div class="detail-cell"><small>artifacts</small><b>${artifacts.length}</b></div><div class="detail-cell"><small>usage records</small><b>${usages.length}</b></div></div><ul class="contract-list"><li><span>reads</span><code>${esc((step.reads||[]).join(', ')||'—')}</code></li><li><span>consumes</span><code>${esc((step.consumes||[]).join(', ')||'—')}</code></li><li><span>produces</span><code>${esc((step.produces||[]).join(', ')||'—')}</code></li><li><span>writes</span><code>${esc((step.writes||[]).join(', ')||'—')}</code></li><li><span>profile</span><code>${esc(step.ai_profile_ref||'—')}</code></li></ul>${human?`<div class="detail-status">Human · ${esc(human.decision)} · ${esc(human.actor?.id)}<br>${esc(human.reason)}</div>`:''}<div class="mini-items">${itemPills}</div>`}function renderTrace(filter='all'){document.querySelectorAll('.filter-button').forEach(b=>b.classList.toggle('active',b.dataset.filter===filter));const keep=e=>filter==='all'||(filter==='human'&&(e.kind.startsWith('human_')||e.kind==='step.waiting'))||(filter==='ai'&&(e.kind.startsWith('model.')||e.kind.startsWith('budget.')))||(filter==='artifact'&&e.kind.startsWith('artifact.'));document.getElementById('event-list').innerHTML=selectedRun.events.filter(keep).map(e=>`<article class="event-row"><span class="event-time">${esc(e.at.slice(11,19))}</span><span class="event-dot"></span><code class="event-kind">${esc(e.kind)}</code><span class="event-payload">${esc(e.step_id||'run')} · ${esc(compact(e.payload))}</span></article>`).join('')}function renderArtifacts(){document.getElementById('artifact-body').innerHTML=selectedRun.artifacts.map(a=>`<tr><td><strong>${esc(a.name)}</strong><br><code>${esc(a.artifact_id)}</code></td><td>${esc(a.producer.step_id)}<br><code>${esc(a.producer.attempt_id)}</code></td><td>${esc(a.media_type)}<br>${esc(a.classification)}</td><td><span class="validity">${esc(a.validity)}</span><br><code>${esc(a.sha256.slice(0,23))}…</code></td><td><code>${esc(compact(a.binding))}</code></td></tr>`).join('')}function renderGovernance(){const b=selectedRun.budget_summary;const pct=Math.min(100,Number(b.settled)/Math.max(Number(b.limit),.000001)*100);document.getElementById('budget-value').textContent=money(b.settled,b.currency);document.getElementById('budget-limit').textContent=`of ${money(b.limit,b.currency)} Run limit`;document.getElementById('budget-fill').style.width=`${pct}%`;document.getElementById('budget-available').textContent=`available ${money(b.available,b.currency)}`;document.getElementById('usage-body').innerHTML=selectedRun.usage_records.map(u=>`<tr><td><code>${esc(u.model_invocation_id)}</code><br>${esc(u.step_id)}</td><td>${esc(u.provider)} / ${esc(u.model_id)}<br>${esc(u.modality)}</td><td><code>${esc(compact(u.usage))}</code></td><td>${esc(u.cost.status)}<br><strong>${money(u.cost.amount,u.cost.currency)}</strong></td><td><code>${esc(u.profile_snapshot.profile_id)}@${esc(u.profile_snapshot.version)}</code></td></tr>`).join('');document.getElementById('profile-list').innerHTML=Object.entries(DATA.profiles).map(([ref,p])=>`<article class="profile-card"><strong>${esc(p.profile_id)} · ${esc(p.version)}</strong><small>${esc(p.model.provider)} / ${esc(p.model.model_id)} · ${esc(p.kind)}</small><div class="profile-pills"><span>${esc(p.model.modality||'text')}</span><span>fallback ${esc(p.model.fallback_policy)}</span><span>transfer ${esc(p.data_policy.external_transfer)}</span><span>retention ${esc(p.data_policy.provider_retention)}</span></div></article>`).join('')}function renderSpec(){const p=selectedRun.process_snapshot;const mappings=[['Business instance','session + task 保持稳定','跨 Run join key'],['Run','一次静态 DAG 执行',selectedRun.run_id],['Step / Attempt','逻辑节点与技术尝试分离',`${selectedRun.steps.length} / ${selectedRun.steps.reduce((n,s)=>n+s.attempts.length,0)}`],['Artifact','hash + binding + provenance',`${selectedRun.artifacts.length} records`],['Human Task','work item + actor + reason + revision',`${selectedRun.human_tasks.length} decisions`],['AI Usage','每 invocation 一条不可变事实',`${selectedRun.usage_records.length} observations`],['Budget','调用前预留、调用后结算',money(selectedRun.budget_summary.settled,selectedRun.budget_summary.currency)],['Revision','新输入创建新 Run',selectedRun.supersedes_run_id||'root run']];document.getElementById('mapping-list').innerHTML=mappings.map(m=>`<div class="mapping-row"><strong>${esc(m[0])}</strong><span>${esc(m[1])}</span><code>${esc(m[2])}</code></div>`).join('');document.getElementById('digest-list').innerHTML=`<div class="digest-row"><small>Process ${esc(p.process_id)}@${esc(p.version)}</small><code>${esc(p.digest)}</code></div><div class="digest-row"><small>Tool Registry ${esc(p.tool_registry_id)}@${esc(p.tool_registry_version)}</small><code>${esc(p.tool_registry_digest)}</code></div>`+p.profiles.map(x=>`<div class="digest-row"><small>AI Profile ${esc(x.profile_id)}@${esc(x.version)}</small><code>${esc(x.digest)}</code></div>`).join('')}document.querySelectorAll('.step-card').forEach(card=>card.addEventListener('click',()=>selectStep(card.dataset.stepId)));document.querySelectorAll('.tab-button').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('.tab-button').forEach(b=>b.classList.toggle('active',b===button));document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.id===button.dataset.tab));window.scrollTo({top:document.querySelector('.tabs').offsetTop-86,behavior:'smooth'})}));document.querySelectorAll('.filter-button').forEach(button=>button.addEventListener('click',()=>renderTrace(button.dataset.filter)));renderRunPicker();selectRun(selectedRun.run_id);
"""


PAGE_ENHANCEMENT_JS = r"""
(() => {
  const bundle = JSON.parse(document.getElementById("demo-data").textContent);
  const profiles = new Map(
    Object.values(bundle.profiles).map((profile) => [profile.profile_id, profile]),
  );
  const escapeHtml = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
  );
  const selectedRun = () => {
    const runId = document.querySelector(".run-button.active")?.dataset.runId
      ?? document.querySelector(".lineage-node.active")?.dataset.runId;
    return bundle.runs.find((run) => run.run_id === runId) ?? bundle.runs.at(-1);
  };
  const enrichUsage = () => {
    const run = selectedRun();
    document.querySelectorAll("#usage-body tr").forEach((row, index) => {
      row.querySelector(".agent-ref")?.remove();
      const usage = run.usage_records[index];
      if (!usage?.agent_execution_id) return;
      const profile = profiles.get(usage.profile_snapshot.profile_id);
      const turn = usage.usage?.provider_units?.agent_turn;
      const limit = profile?.budget?.max_turns;
      const turnLabel = turn == null ? "" : ` · turn ${turn}${limit == null ? "" : `/${limit}`}`;
      row.cells[0].insertAdjacentHTML(
        "beforeend",
        `<span class="agent-ref" data-agent-execution-id="${escapeHtml(usage.agent_execution_id)}">Agent · ${escapeHtml(usage.agent_execution_id)}${escapeHtml(turnLabel)}</span>`,
      );
    });
  };
  const enrichArtifacts = () => {
    const run = selectedRun();
    document.querySelectorAll("#artifact-body tr").forEach((row, index) => {
      if (row.querySelector(".artifact-derivation")) return;
      const artifact = run.artifacts[index];
      const step = run.steps.find((item) => item.step_id === artifact?.producer?.step_id);
      const producer = [
        ...(step?.attempts ?? []),
        ...(step?.fanout_items ?? []),
      ].find((item) => item.attempt_id === artifact?.producer?.attempt_id);
      const value = producer?.input_snapshot_hash ?? "missing producer digest";
      row.insertAdjacentHTML(
        "beforeend",
        `<td class="artifact-derivation"><code title="${escapeHtml(value)}">${escapeHtml(value.slice(0, 23))}${value.startsWith("sha256:") ? "…" : ""}</code><small>Runtime-computed once on producer</small></td>`,
      );
    });
  };
  const enrichArtifactCopy = () => {
    const run = selectedRun();
    const metric = [...document.querySelectorAll("#metric-grid .metric-card")].find(
      (card) => card.querySelector("small")?.textContent === "Artifact",
    );
    const metricNote = metric?.querySelector("em");
    if (metricNote) {
      const fanout = run.steps.reduce((count, step) => count + step.fanout_items.length, 0);
      metricNote.textContent = `${fanout} fanout items · canonical · runtime-hashed`;
    }
    const mapping = [...document.querySelectorAll("#mapping-list .mapping-row")].find(
      (row) => row.querySelector("strong")?.textContent === "Artifact",
    );
    if (mapping) mapping.querySelector("span").textContent = "canonical only · Runtime hash · producer provenance · binding on demand";
  };
  const enrichProfiles = () => {
    document.querySelectorAll("#profile-list .profile-card").forEach((card) => {
      const profileId = card.querySelector("strong")?.textContent?.split(" · ")[0];
      const profile = profiles.get(profileId);
      const pills = card.querySelector(".profile-pills");
      if (profile?.kind !== "agent" || !pills || pills.querySelector(".agent-limits")) return;
      pills.insertAdjacentHTML(
        "beforeend",
        `<span class="agent-limits">max ${escapeHtml(profile.budget.max_turns)} turns</span><span class="agent-limits">max ${escapeHtml(profile.budget.max_tool_calls)} tool calls</span>`,
      );
    });
  };
  new MutationObserver(enrichUsage).observe(document.getElementById("usage-body"), { childList: true });
  new MutationObserver(enrichArtifacts).observe(document.getElementById("artifact-body"), { childList: true });
  new MutationObserver(enrichArtifactCopy).observe(document.getElementById("metric-grid"), { childList: true });
  new MutationObserver(enrichArtifactCopy).observe(document.getElementById("mapping-list"), { childList: true });
  new MutationObserver(enrichProfiles).observe(document.getElementById("profile-list"), { childList: true });
  enrichUsage();
  enrichArtifacts();
  enrichArtifactCopy();
  enrichProfiles();
})();
"""


STORAGE_JS = r"""
(() => {
  const indexes = DATA.storage_indexes ?? [];
  const renderStorage = () => {
    const index = indexes.find((item) => item.run_id === selectedRun.run_id) ?? indexes.at(-1);
    if (!index) return;
    const entries = index.entries ?? [];
    const materialized = entries.filter((entry) => entry.materialization === "materialized");
    const contracts = entries.filter((entry) => entry.materialization === "layout_contract");
    const authorities = new Set(entries.map((entry) => entry.authority));
    document.getElementById("storage-run-summary").innerHTML =
      `<b>${esc(index.run_id)}</b> · ${materialized.length} 个真实 bundle 文件 · ${contracts.length} 个兼容布局 locator · ${authorities.size} 类 authority。`;
    document.getElementById("storage-entry-body").innerHTML = entries.map((entry) => `
      <tr>
        <td><strong>${esc(entry.owner_type)} · ${esc(entry.owner_id)}</strong><br><code>${esc(entry.purpose)}</code></td>
        <td>${esc(entry.authority)}<br><small>${esc(entry.retention_class ?? "—")}</small></td>
        <td><code>${esc(entry.base)}://${esc(entry.locator)}</code><br><small>${esc(entry.description)}</small></td>
        <td><span class="locator-state ${esc(entry.materialization)}">${esc(entry.materialization)}</span>${entry.size == null ? "" : `<br><small>${entry.size} bytes</small>`}</td>
      </tr>`).join("");
    const diagnostic = entries.filter(
      (entry) => entry.purpose === "diagnostic_log" && entry.materialization === "materialized",
    );
    const visible = diagnostic.slice(0, 6);
    document.getElementById("diagnostic-ref-list").innerHTML = visible.map((entry, itemIndex) => `
      <li><span>${itemIndex === 0 ? "run" : "attempt"}</span><code>${esc(entry.locator)}</code><small class="diagnostic-file">${entry.size ?? 0} bytes · ${esc(entry.sha256?.slice(0, 24) ?? "no hash")}…</small></li>`).join("") +
      (diagnostic.length > visible.length ? `<li><span>more</span><code>另有 ${diagnostic.length - visible.length} 个诊断文件，见下方完整 index</code></li>` : "");
    const stepLayout = entries.find(
      (entry) => entry.purpose === "diagnostic_log" && entry.materialization === "layout_contract",
    );
    document.getElementById("storage-example-step").textContent = stepLayout
      ? `${stepLayout.owner_id} 的 Report locator`
      : "当前 Step 示例";
    document.getElementById("storage-index-json").textContent = JSON.stringify(index, null, 2);
  };
  new MutationObserver(renderStorage).observe(document.getElementById("current-run-label"), {
    childList: true,
    characterData: true,
    subtree: true,
  });
  renderStorage();
})();
"""


GRAPH_CONTROL_JS = r"""
(() => {
  const flattenDependency = (dependency, kind = "all") => {
    if (dependency?.step_id) return [{ source: String(dependency.step_id), kind }];
    return (dependency?.any_of ?? []).flatMap((item) => flattenDependency(item, "any"));
  };
  const edges = DATA.process.steps.flatMap((step) =>
    (step.depends_on ?? []).flatMap((dependency) =>
      flattenDependency(dependency).map((source) => ({
        ...source,
        target: String(step.id),
        guarded: Boolean(step.when),
      })),
    ),
  );
  const graphNodes = [...document.querySelectorAll(".dag-map-node")];
  const graphNode = (stepId) => graphNodes.find((node) => node.dataset.stepId === stepId);
  const canvas = document.querySelector(".dag-map-canvas");
  const svg = document.querySelector(".dag-edge-layer");

  const drawEdges = () => {
    if (!canvas || !svg || getComputedStyle(document.querySelector(".dag-map-scroll")).display === "none") return;
    const canvasRect = canvas.getBoundingClientRect();
    const accent = getComputedStyle(document.body).getPropertyValue("--accent").trim() || "#2dd4bf";
    const accent2 = getComputedStyle(document.body).getPropertyValue("--accent-2").trim() || "#60a5fa";
    svg.setAttribute("viewBox", `0 0 ${canvas.offsetWidth} ${canvas.offsetHeight}`);
    svg.innerHTML = `<defs><marker id="dag-arrow" viewBox="0 0 10 10" refX="8.2" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${accent}"></path></marker><marker id="dag-arrow-guard" viewBox="0 0 10 10" refX="8.2" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${accent2}"></path></marker></defs>`;
    for (const edge of edges) {
      const source = graphNode(edge.source);
      const target = graphNode(edge.target);
      if (!source || !target) continue;
      const sourceRect = source.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const x1 = sourceRect.right - canvasRect.left;
      const y1 = sourceRect.top - canvasRect.top + sourceRect.height / 2;
      const x2 = targetRect.left - canvasRect.left;
      const y2 = targetRect.top - canvasRect.top + targetRect.height / 2;
      const bend = Math.max(32, Math.min(105, (x2 - x1) * 0.46));
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
      path.setAttribute("class", `dag-edge${edge.guarded ? " is-guarded" : ""}${edge.kind === "any" ? " is-any" : ""}`);
      path.setAttribute("data-source", edge.source);
      path.setAttribute("data-target", edge.target);
      path.setAttribute("marker-end", edge.guarded ? "url(#dag-arrow-guard)" : "url(#dag-arrow)");
      svg.appendChild(path);
    }
  };

  const syncGraphState = () => {
    for (const node of graphNodes) {
      const record = runStep(node.dataset.stepId);
      node.classList.remove("is-completed", "is-skipped", "is-failed", "is-waiting", "is-selected");
      if (record?.status) node.classList.add(`is-${record.status}`);
      node.classList.toggle("is-selected", node.dataset.stepId === selectedStepId);
      const label = node.querySelector(".dag-node-state");
      if (label && record) label.textContent = statusLabel(record.status);
    }
  };

  const stepLabel = (stepId) => {
    const title = DATA.metadata?.step_labels?.[stepId]?.title;
    return title ? `${title} (${stepId})` : stepId;
  };
  const dependencyText = (dependency) => {
    if (dependency?.step_id) return stepLabel(dependency.step_id);
    const sources = (dependency?.any_of ?? []).map(dependencyText);
    return `any(${sources.join(" | ")})`;
  };
  const enrichDetail = () => {
    const detail = document.getElementById("detail-inner");
    const list = detail?.querySelector(".contract-list");
    const step = DATA.process.steps.find((item) => item.id === selectedStepId);
    if (!list || !step || list.querySelector(".dependency-contract")) return;
    const upstream = (step.depends_on ?? []).map(dependencyText).join(", ") || "root · 无前置";
    const downstream = edges.filter((edge) => edge.source === step.id).map((edge) => stepLabel(edge.target)).join(", ") || "terminal · 无下游";
    list.insertAdjacentHTML("afterbegin", `<li class="dependency-contract"><span>前置步骤</span><code>${esc(upstream)}</code></li><li class="dependency-contract"><span>下游步骤</span><code>${esc(downstream)}</code></li>`);
  };

  for (const node of [...graphNodes, ...document.querySelectorAll(".dependency-mobile-row")]) {
    node.addEventListener("click", () => {
      selectStep(node.dataset.stepId);
      syncGraphState();
      enrichDetail();
    });
  }
  new MutationObserver(() => requestAnimationFrame(syncGraphState)).observe(
    document.querySelector(".stage-grid"),
    { subtree: true, attributes: true, attributeFilter: ["class"] },
  );
  new MutationObserver(enrichDetail).observe(document.getElementById("detail-inner"), { childList: true });
  if (typeof ResizeObserver !== "undefined" && canvas) new ResizeObserver(drawEdges).observe(canvas);
  window.addEventListener("resize", () => requestAnimationFrame(drawEdges));
  document.fonts?.ready?.then(drawEdges);
  syncGraphState();
  enrichDetail();
  requestAnimationFrame(drawEdges);
})();
"""


LOCALIZATION_JS = r"""
(() => {
  const localizedStep = (step) => DATA.metadata?.step_labels?.[step.id] ?? {
    title: step.tool,
    description: step.description ?? "",
  };
  const localizedStage = (stage) => DATA.metadata?.stage_labels?.[stage] ?? stage;
  const localizedOutcome = (outcomeId) => DATA.metadata?.outcome_labels?.[outcomeId] ?? outcomeId;

  const originalSelectStep = selectStep;
  selectStep = (stepId) => {
    originalSelectStep(stepId);
    const step = DATA.process.steps.find((item) => item.id === stepId);
    const detail = document.getElementById("detail-inner");
    if (!step || !detail) return;
    const localized = localizedStep(step);
    const kicker = detail.querySelector(".detail-kicker");
    const title = detail.querySelector(".detail-title");
    const description = detail.querySelector(".detail-description");
    if (kicker) kicker.textContent = `${step.id} · ${localizedStage(step.stage)} / ${step.stage}`;
    if (title) title.textContent = localized.title;
    if (description) description.textContent = localized.description;
    detail.querySelector(".detail-tool-contract")?.remove();
    description?.insertAdjacentHTML(
      "afterend",
      `<div class="detail-tool-contract"><span>Tool contract</span><code>${esc(step.tool)}</code></div>`,
    );
  };

  const originalRenderSummary = renderSummary;
  renderSummary = () => {
    originalRenderSummary();
    const outcome = document.querySelector("#metric-grid .metric-card:first-child strong");
    if (outcome) {
      outcome.textContent = localizedOutcome(selectedRun.outcome);
      outcome.title = `${localizedOutcome(selectedRun.outcome)} · ${selectedRun.outcome}`;
    }
    const caption = document.querySelector("#metric-grid .metric-card:first-child em");
    if (caption) caption.textContent = `Run ${selectedRun.run_sequence} · ${statusLabel(selectedRun.status)}`;
  };

  const originalRenderLineage = renderLineage;
  renderLineage = () => {
    originalRenderLineage();
    document.querySelectorAll(".lineage-node").forEach((node) => {
      const run = DATA.runs.find((item) => item.run_id === node.dataset.runId);
      if (!run) return;
      const kind = node.querySelector("small");
      const outcome = node.querySelector("span");
      if (kind) kind.textContent = run.supersedes_run_id ? "修订前一次运行" : "业务实例首次运行";
      if (outcome) {
        outcome.textContent = localizedOutcome(run.outcome);
        outcome.title = run.outcome;
      }
    });
  };

  renderRunPicker = () => {
    const host = document.getElementById("run-picker");
    host.innerHTML = '<span class="run-picker-label">选择运行记录</span>' + DATA.runs.map((run) => `
      <button class="run-button" data-run-id="${esc(run.run_id)}">
        <small>RUN ${run.run_sequence}${run.supersedes_run_id ? " · REVISION" : ""} · ${esc(run.outcome)}</small>
        ${esc(localizedOutcome(run.outcome))}
      </button>`).join("");
    host.querySelectorAll("button").forEach((button) =>
      button.addEventListener("click", () => selectRun(button.dataset.runId))
    );
  };

  const localizeCurrentRun = () => {
    const label = document.getElementById("current-run-label");
    const value = `${statusLabel(selectedRun.status)} · ${localizedOutcome(selectedRun.outcome)}`;
    if (label && label.textContent !== value) {
      label.textContent = value;
      label.title = `${selectedRun.status} · ${selectedRun.outcome}`;
    }
  };
  new MutationObserver(localizeCurrentRun).observe(
    document.getElementById("current-run-label"),
    { childList: true, characterData: true, subtree: true },
  );

  renderRunPicker();
  selectRun(selectedRun.run_id);
  localizeCurrentRun();
})();
"""


def _page_html(metadata: dict[str, Any], process: dict[str, Any], registry: dict[str, Any], runs: list[dict[str, Any]], profiles: dict[str, Any], storage_indexes: list[dict[str, Any]]) -> str:
    bundle = {"metadata": metadata, "process": process, "registry": registry, "runs": runs, "profiles": profiles, "storage_indexes": storage_indexes}
    latest = runs[-1]
    page_js = PAGE_JS.replace(
        "all hash-bound",
        "canonical · runtime-hashed",
    ).replace(
        "hash + binding + provenance",
        "canonical only · runtime hash + producer provenance",
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="{h(metadata['subtitle'])}">
  <title>{h(metadata['title'])} · FlowSpec Demo</title>
  <style>{PAGE_CSS}{EXPLAINER_CSS}{READABILITY_CSS}{GRAPH_CONTROL_CSS}{DOCUMENT_THEME_CSS}{STORAGE_CSS}{ARTIFACT_CSS}</style>
</head>
<body style="--accent:{h(metadata['accent'])};--accent-2:{h(metadata['accent_secondary'])};--tint:{h(metadata['surface_tint'])}">
  <header class="shell topbar">
    <a class="brand" href="../index.html"><span class="brand-mark">F</span><span>FlowSpec <small>/ 业务流程示例</small></span></a>
  </header>
  <section class="hero">
    <svg class="hero-network" viewBox="0 0 700 420" aria-hidden="true"><path d="M30 208 C140 20 245 375 354 180 S545 25 675 220"/><path d="M20 305 C140 180 250 390 390 245 S575 150 690 330"/><line x1="100" y1="80" x2="580" y2="365"/><circle cx="103" cy="81" r="7"/><circle cx="352" cy="180" r="8"/><circle cx="579" cy="365" r="7"/><circle cx="391" cy="245" r="6"/></svg>
    <div class="shell hero-grid">
      <div><p class="eyebrow">{h(metadata['eyebrow'])}</p><h1>{h(metadata['title'])}</h1><p class="subtitle">{h(metadata['subtitle'])}</p></div>
      <aside class="hero-aside"><div class="hero-aside-top"><div><small>{h(metadata['hero_metric_label'])}</small><strong>{h(metadata['hero_metric_value'])}</strong></div><span class="pulse"></span></div><div class="definition-facts"><div class="definition-fact"><small>Process</small><b>{h(process['process_id'])}</b></div><div class="definition-fact"><small>Version</small><b>{h(process['version'])}</b></div><div class="definition-fact"><small>Owner</small><b>{h(metadata['business_owner'])}</b></div><div class="definition-fact"><small>Contract</small><b>{h(process['contract_level'])}</b></div></div></aside>
    </div>
    {_executive_tldr_markup(metadata)}
  </section>
  <div class="runbar-wrap"><div class="shell runbar"><div class="run-picker" id="run-picker"></div><div class="current-run-state"><span class="state-dot"></span><strong id="current-run-label">{h(latest['status'])} · {h(latest['outcome'])}</strong></div></div></div>
  <main class="shell">
    <nav class="tabs" aria-label="Demo views"><button class="tab-button active" data-tab="flow" type="button">业务流程</button><button class="tab-button" data-tab="trace" type="button">运行记录</button><button class="tab-button" data-tab="artifacts" type="button">Artifact</button><button class="tab-button" data-tab="storage" type="button">存储与日志</button><button class="tab-button" data-tab="governance" type="button">AI / 成本治理</button><button class="tab-button" data-tab="spec" type="button">规范透视</button></nav>
    <section class="tab-panel active" id="flow">
      <div class="metric-grid" id="metric-grid"></div>
      {_explainer_markup(metadata)}
      <div class="section-head"><div><p class="eyebrow">Business revision chain</p><h2>一次业务实例，多次有界 Run</h2></div><p>输入修订不在 DAG 内画回边；它创建新的 Run，并冻结本次 Process、Tool Registry、AI Profile 与输入哈希。</p></div>
      <div class="lineage" id="lineage"></div>
      <div class="section-head"><div><p class="eyebrow">Executable DAG</p><h2>流程、契约与实际状态同屏</h2></div><p>卡片编号用于阅读；连线依赖、Artifact、guard 和 outcome 才决定执行。fork 表示一对多并行，join 表示多上游汇合；点击任一步查看 Attempt、I/O、fanout、Human Task 与 Usage。</p></div>
      {_dependency_graph_markup(process, metadata)}
      <div class="flow-layout"><div class="flow-board"><div class="flow-scroll-hint" role="note"><span class="desktop-flow-hint"><b>阅读提示</b> 从左到右按 Stage 阅读；横向滚动可查看全部阶段。</span><span class="mobile-flow-hint"><b>阅读提示</b> 按 Stage 向下阅读；卡片状态来自当前 Run。</span><span>中文业务动作优先；Step ID 与 Tool key 保留为次级技术标识</span></div><div class="stage-grid">{_stage_markup(process, metadata)}</div></div><aside class="detail-panel"><div class="detail-accent"></div><div class="detail-inner" id="detail-inner"></div></aside></div>
      <div class="section-head"><div><p class="eyebrow">Completion contract</p><h2>正常业务结果不是只有“成功一条路”</h2></div><p><code>exactly_one_outcome</code> 让拒绝、补件、修订等正常结果也能完整终结；未激活分支递归关闭为 skipped。</p></div>
      <div class="outcome-grid">{_outcome_markup(process, metadata)}</div>
    </section>
    <section class="tab-panel" id="trace">
      <div class="section-head"><div><p class="eyebrow">Append-only view</p><h2>从 Run 到每个事实事件</h2></div><p>快照供 UI 收敛，事件供增量追踪；Human Task、预算、模型调用和 Artifact 发布都保留明确身份。</p></div>
      <div class="trace-layout"><div class="panel-card"><div class="filter-row"><button class="filter-button active" data-filter="all">全部</button><button class="filter-button" data-filter="human">人工</button><button class="filter-button" data-filter="ai">AI / Budget</button><button class="filter-button" data-filter="artifact">Artifact</button></div><div class="event-list" id="event-list"></div></div><aside><div class="panel-card"><h3>运行记录文件</h3><p>页面内嵌同一份记录；仓库同时保留可独立校验的 JSON / NDJSON。</p><ul class="contract-list"><li><span>snapshot</span><code>runs/&lt;run&gt;/run.json</code></li><li><span>events</span><code>events.ndjson</code></li><li><span>usage</span><code>usage.json</code></li><li><span>budget</span><code>budget-ledger.json</code></li><li><span>storage</span><code>storage-index.json</code></li><li><span>diagnostic</span><code>logs/&lt;step&gt;/&lt;attempt&gt;/*</code></li><li><span>definition</span><code>definition/*.snapshot.json</code></li></ul></div><div class="panel-card"><h3>事件、日志与财务事实分开</h3><p>durable Event 给出审计时间线；Attempt 日志用于诊断；流式 <code>usage_updated</code> 只服务观测。最终计量以不可变 UsageRecord 为准，纠错用 superseding observation。</p></div></aside></div>
    </section>
    <section class="tab-panel" id="artifacts">
      <div class="section-head"><div><p class="eyebrow">Typed evidence plane</p><h2>只给正式产物足够的治理，不给所有文件加税</h2></div><p>本页只列经 finalize 发布、会跨 Step 交接或成为业务结果的 canonical Artifact。内容 hash 与 producer 输入摘要由 Runtime 自动生成；业务 binding 只在确需 join/re-bind/跨 Run 对齐时声明。</p></div>
      <div class="artifact-scope-grid">
        <article class="artifact-scope-card"><small>Author burden · minimal</small><h3>Tool 只交 payload</h3><p>流程作者声明 output Schema；只有存在业务对齐需求才写 <code>binding_keys</code>。Tool 不计算 hash、不生成 Artifact ID，也不复制 Run 元数据。</p></article>
        <article class="artifact-scope-card"><small>Runtime · automatic</small><h3>finalize 一次计算</h3><p>Runtime 生成版本 <code>artifact_id</code>、流式计算内容 <code>sha256/size</code>；声明输入与冻结 Tool/Profile 的摘要只存 producer Attempt 一次。</p></article>
        <article class="artifact-scope-card excluded"><small>Out of scope · lightweight</small><h3>临时文件明确排除</h3><p><code>Working / logs / Prompt / .staging / SessionOutput projection</code> 不承担业务 binding 或派生摘要，也不能被当作可复用正式产物。</p></article>
      </div>
      <aside class="artifact-scope-note"><b>为什么不在 Artifact 重复输入 hash？</b><span>一个 Attempt 可能发布多个 Artifact。每条记录只保留 producer <code>attempt_id</code>，页面从 Attempt/Fanout Item 解析唯一的 <code>input_snapshot_hash</code>；便携导出需要时才内联。</span></aside>
      <aside class="artifact-scope-note"><b>为什么四个 Demo 的 binding 都非空？</b><span>贷款、监管报告、尽调和视频产物都需要跨 Run 按 application / entity / matter / project 对齐；这是场景需要，不是全局强制。无 join、re-bind 或跨 Run 对齐的一次性正式产物可省略 <code>binding_keys</code>。</span></aside>
      <div class="panel-card"><p class="artifact-table-hint"><b>手机阅读提示：</b>表格可横向滚动；右侧继续显示内容 hash、按需 business binding 与 producer input digest。</p><div class="table-wrap artifact-table-wrap"><table><thead><tr><th>Artifact version</th><th>Producer</th><th>Contract</th><th>Content / validity</th><th>Business binding · opt-in</th><th>Producer input digest</th></tr></thead><tbody id="artifact-body"></tbody></table></div></div>
    </section>
    <section class="tab-panel" id="storage">
      {_storage_markup()}
    </section>
    <section class="tab-panel" id="governance">
      <div class="section-head"><div><p class="eyebrow">Invocation-level governance</p><h2>AI 调用、媒体单位与预算可逐笔解释</h2></div><p>Mock 工具不联网，但仍必须经过冻结 Profile、调用前预算预留、调用后结算，并按 invocation 记录 token / media units 与 provider cost；一个 Agent Step 内的多轮调用以同一 <code>agent_execution_id</code> 归组。</p></div>
      <div class="governance-grid"><div><div class="budget-hero"><div class="budget-top"><div><small>settled provider cost</small><strong id="budget-value">—</strong></div><small id="budget-limit">—</small></div><div class="budget-track"><div class="budget-fill" id="budget-fill"></div></div><div class="budget-caption"><span>0</span><span id="budget-available">—</span></div></div><div class="panel-card"><h3>Usage observations</h3><p>每行是一条不可变调用观测；Agent 多轮会显示共同执行 ID 与 turn。金额是 decimal string；estimated 不冒充 invoice-final，customer/internal charge 不覆盖 provider cost。</p><div class="table-wrap"><table><thead><tr><th>Invocation</th><th>Resolved model</th><th>Usage</th><th>Provider cost</th><th>Frozen profile</th></tr></thead><tbody id="usage-body"></tbody></table></div></div></div><aside class="panel-card"><h3>Resolved AI profiles</h3><p>模型、fallback、数据传输、留存、Agent tools/network、max turns/tool calls 与多模态预算随 Run 冻结。</p><div class="profile-list" id="profile-list"></div></aside></div>
    </section>
    <section class="tab-panel" id="spec">
      <div class="section-head"><div><p class="eyebrow">FlowSpec exposed</p><h2>UI 上每一块，都有权威契约来源</h2></div><p>此页不是流程图片：它把业务概念、静态定义、物化运行事实和 OpenCrew 可演进的实现边界同时展示。</p></div>
      {_run_control_markup(metadata)}
      <div class="principle-grid"><article class="principle"><span>P1</span><strong>Run 内 DAG，生命周期跨 Run</strong><p>返工与补件不污染可静态校验的拓扑。</p></article><article class="principle"><span>P3</span><strong>副作用决定恢复策略</strong><p>pure / idempotent 可重算；reconcilable 先查后补。</p></article><article class="principle"><span>P4</span><strong>canonical Artifact 最小充分</strong><p>Runtime 自动算版本 ID/hash；派生摘要存 producer Attempt，binding 按需。临时文件排除。</p></article><article class="principle"><span>P6</span><strong>人工介入是 Work Item</strong><p>创建后 waiting，决策绑定输入、actor、reason 与 expected revision。</p></article><article class="principle"><span>P8</span><strong>资源与预算原子门控</strong><p>调用前 reserve，执行后 settle/release；并发资源需租约与 fencing。</p></article><article class="principle"><span>P11</span><strong>日志与存储分平面</strong><p>Event、诊断、Usage 与服务日志各有权威；locator 带 base，目录增量兼容 OpenCrew。</p></article><article class="principle"><span>AI</span><strong>Profile 与 Usage 分离</strong><p>Profile 是调用前政策；Usage/Cost 是调用后不可变事实。</p></article></div>
      <div class="spec-grid" style="margin-top:16px"><div class="panel-card"><h3>概念 → 当前 Run</h3><div class="mapping-list" id="mapping-list"></div></div><aside class="panel-card"><h3>冻结定义摘要</h3><p>恢复、审计和回放都引用这些 digest，而不是悄悄采用最新配置。</p><div class="digest-list" id="digest-list"></div></aside></div>
      <div class="panel-card"><h3>机器可读原文</h3><p>HTML 只是投影；Process 与 Tool Registry JSON 是静态定义，Run JSON 是执行快照，StorageIndex 则明确每类证据的权威与物理 locator。</p><details><summary>Process definition · {h(process['process_id'])}@{h(process['version'])}</summary><pre>{h(json.dumps(process, ensure_ascii=False, indent=2))}</pre></details><details><summary>Tool Registry · {h(registry['registry_id'])}@{h(registry['version'])}</summary><pre>{h(json.dumps(registry, ensure_ascii=False, indent=2))}</pre></details><details><summary>Selected Run JSON（页面切换 Run 后请在“运行记录”查看动态投影）</summary><pre>{h(json.dumps(latest, ensure_ascii=False, indent=2))}</pre></details></div>
    </section>
  </main>
  <footer class="shell footer"><strong>FlowSpec v0.4 · 目标契约 Demo</strong><span>业务流程 · 运行记录 · Artifact · 存储日志 · AI / 成本治理</span></footer>
  <noscript><div class="shell noscript">本页面的静态 Process 原文仍可阅读；启用 JavaScript 可切换 Run、步骤详情和治理视图。</div></noscript>
  <script type="application/json" id="demo-data">{json_for_script(bundle)}</script>
  <script>{page_js}</script>
  <script>{PAGE_ENHANCEMENT_JS}</script>
  <script>{STORAGE_JS}</script>
  <script>{GRAPH_CONTROL_JS}</script>
  <script>{LOCALIZATION_JS}</script>
</body>
</html>
"""


def generate_scenario_page(scenario_dir: Path) -> Path:
    scenario_dir = scenario_dir.resolve()
    metadata = load_json(scenario_dir / "demo.json")
    process = load_json(scenario_dir / "process.json")
    registry = load_json(scenario_dir / "tool_registry.json")
    cases = load_json(scenario_dir / "cases.json")
    if not all(isinstance(value, dict) for value in (metadata, process, registry)) or not isinstance(cases, list):
        raise TypeError("invalid demo source data")
    runs = [load_json(scenario_dir / "runs" / str(case["run_id"]) / "run.json") for case in cases]
    storage_indexes = [load_json(scenario_dir / "runs" / str(case["run_id"]) / "storage-index.json") for case in cases]
    profiles: dict[str, Any] = {}
    for step in process.get("steps") or []:
        ref = step.get("ai_profile_ref")
        if ref and ref not in profiles:
            profiles[str(ref)] = load_json(scenario_dir / str(ref))
    target = scenario_dir / "index.html"
    target.write_text(_page_html(metadata, process, registry, runs, profiles, storage_indexes), encoding="utf-8")
    return target


LANDING_CSS = r"""
:root{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;color-scheme:light;--bg:#f3f6f8;--line:#d5dfe3;--text:#182d36;--muted:#526a74}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#fbfcfd,#f3f6f8 520px);color:var(--text)}
main{width:min(1180px,calc(100% - 40px));margin:auto;padding:52px 0}
.back{display:inline-flex;margin-bottom:30px;color:var(--muted);text-decoration:none;font-size:12px}
.back:hover{color:var(--text)}
.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.16em;color:#087f6d;font-weight:800}
h1{max-width:1040px;margin:14px 0 20px;font-size:clamp(38px,4vw,54px);line-height:1.08;letter-spacing:-.04em}
header p{max-width:900px;color:var(--muted);font-size:16px;line-height:1.75}
.boundary{max-width:1040px;margin-top:20px;padding:15px 17px;border:1px solid #d7e1e4;border-radius:13px;background:#fff;color:var(--muted);font-size:14px;line-height:1.7}
.boundary strong{color:var(--text)}
.boundary .limit{color:#875a05}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:38px}
.card{--accent:#2d8f80;position:relative;min-height:275px;border:1px solid var(--line);border-radius:17px;padding:25px;background:linear-gradient(145deg,color-mix(in srgb,var(--accent) 5%,#fff),#fff);color:var(--text);text-decoration:none;overflow:hidden;transition:.25s;box-shadow:0 2px 8px rgba(22,47,58,.045)}
.card:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--accent) 65%,transparent);box-shadow:0 8px 22px rgba(22,47,58,.09)}
.number{font-size:11px;color:var(--accent);font-weight:900;letter-spacing:.13em}
.card h2{font-size:27px;line-height:1.16;letter-spacing:-.03em;margin:34px 0 11px;max-width:490px}
.card p{font-size:14px;line-height:1.7;color:var(--muted);max-width:510px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:20px}
.chips span{font-size:10.5px;border:1px solid var(--line);padding:5px 8px;border-radius:999px;background:#fff;color:#506872}
.arrow{position:absolute;right:24px;top:22px;width:38px;height:38px;border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);border-radius:50%;display:grid;place-items:center;color:var(--accent);font-size:18px}
.glow{display:none}
.foot{border-top:1px solid var(--line);margin-top:50px;padding-top:24px;color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:20px}
@media(max-width:780px){main{width:min(100% - 24px,1180px);padding-top:34px}
.back{margin-bottom:28px}
.grid{grid-template-columns:1fr}
.card{min-height:0}
.card h2{font-size:25px}
h1{font-size:34px}
.foot{display:block}
.foot span{display:block;margin-top:8px}
}
"""


def generate_landing_page() -> Path:
    preferred_order = ("loan-approval", "bank-risk-report", "due-diligence", "opencrew-video")
    order_index = {name: index for index, name in enumerate(preferred_order)}
    scenarios = []
    scenario_dirs = sorted(
        (path for path in DEMO_ROOT.iterdir() if path.is_dir() and (path / "demo.json").exists()),
        key=lambda path: (order_index.get(path.name, len(preferred_order)), path.name),
    )
    for scenario_dir in scenario_dirs:
        metadata = load_json(scenario_dir / "demo.json")
        process = load_json(scenario_dir / "process.json")
        cases = load_json(scenario_dir / "cases.json")
        scenarios.append((scenario_dir.name, metadata, process, cases))
    cards = []
    for index, (name, metadata, process, cases) in enumerate(scenarios, start=1):
        cards.append(f"""<a class="card" href="{h(name)}/index.html" style="--accent:{h(metadata['accent'])}"><span class="number">SCENARIO {index:02d}</span><span class="arrow">↗</span><h2>{h(metadata['title'])}</h2><p>{h(metadata['subtitle'])}</p><div class="chips"><span>{len(process.get('steps') or [])} 个步骤</span><span>{len(cases)} 次 Run</span><span>{len((process.get('completion') or {}).get('outcomes') or [])} 种业务结果</span></div><span class="glow"></span></a>""")
    target = DEMO_ROOT / "index.html"
    target.write_text(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FlowSpec · 四场景可执行 Demo</title><style>{LANDING_CSS}</style></head><body><main><header><a class="back" href="../index.html">← 返回 FlowSpec 规范综述</a><p class="eyebrow">FlowSpec v0.4 · executable validation set</p><h1>四种业务，同一套可解释的编排契约</h1><p>每个场景按同一阅读顺序展示业务结果、中文步骤与依赖箭头、两次 Run 修订、人工责任、产物血缘、日志物理落点和逐笔成本；决策层可以理解控制边界，开发团队可以继续下钻机器契约。</p><aside class="boundary"><strong>它证明：</strong>契约自洽、拓扑闭合、确定性重建和 UI 投影一致。 <strong class="limit">它不证明：</strong>分布式 claim / lease / fencing、真实并发、进程崩溃恢复、通用范围执行或 reconcile worker 已完成；这些仍是 proposed 能力。</aside></header><section class="grid">{''.join(cards)}</section><footer class="foot"><strong>OpenCrew / docs / WorkflowSpec / demos</strong><span>Process → Tool → Run → Artifact → Event → Usage · 2026-07-24</span></footer></main></body></html>""", encoding="utf-8")
    return target


def generate_all() -> list[Path]:
    pages = [generate_scenario_page(path) for path in sorted(p for p in DEMO_ROOT.iterdir() if p.is_dir() and (p / "process.json").exists())]
    pages.append(generate_landing_page())
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate standalone FlowSpec demo HTML pages")
    parser.add_argument("scenario", nargs="?", help="Scenario directory name; omit to build all")
    args = parser.parse_args()
    if args.scenario:
        generate_scenario_page(DEMO_ROOT / args.scenario)
    else:
        generate_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
