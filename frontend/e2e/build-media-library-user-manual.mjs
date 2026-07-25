import assert from "node:assert/strict";
import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, join, resolve } from "node:path";
import { repoRoot } from "./media-library-real-helpers.mjs";

const setupRoot = resolve(
  repoRoot,
  "frontend/e2e/artifacts/media-library-long-chinese-talking-head",
);
const acceptanceRoot = resolve(
  repoRoot,
  "frontend/e2e/artifacts/media-library-visible-acceptance",
);
const performanceRoot = resolve(
  repoRoot,
  "frontend/e2e/artifacts/media-library-dscf0157-performance-ui",
);
const longWindowRoot = resolve(
  repoRoot,
  "frontend/e2e/artifacts/media-library-long-window-regression",
);
const silentVisualRoot = resolve(
  repoRoot,
  "frontend/e2e/artifacts/media-library-silent-visual-search",
);
const setupDir = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_SETUP_DIR || latestArtifact(setupRoot),
);
const acceptanceDir = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_ACCEPTANCE_DIR
    || latestArtifact(acceptanceRoot),
);
const performanceDir = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_PERFORMANCE_DIR
    || latestArtifactWithFiles(
      performanceRoot,
      "dscf0157-performance-ui-report.json",
      [
        "01-dscf0157-editor-light-desktop.png",
        "02-dscf0157-editor-light-mobile.png",
        "03-dscf0157-no-audio-business-state.png",
      ],
    ),
);
const performanceUploadDir = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_PERFORMANCE_UPLOAD_DIR
    || latestArtifactWithFiles(
      performanceRoot,
      "dscf0157-performance-ui-report.json",
      ["00-dscf0157-concurrent-upload-completed.png"],
    ),
);
const longWindowDir = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_LONG_WINDOW_DIR
    || latestArtifactWithFiles(
      longWindowRoot,
      "report.json",
      [
        "01-dialogue-complete-review-recommendation.png",
        "02-five-visual-analysis-windows.png",
        "03-multiple-composite-segments.png",
        "04-storyboard-explicit-matched-fragments.png",
        "05-matched-fragment-opened-in-editor.png",
      ],
    ),
);
const silentR1Dir = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_SILENT_R1_DIR
    || latestArtifactWithFiles(
      silentVisualRoot,
      "r1-browser-e2e-report.json",
      ["r1-four-frame-detail-desktop.png", "r1-storyboard-visual-hit.png"],
    ),
);
const silentR2Dir = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_SILENT_R2_DIR
    || latestArtifactWithFiles(
      silentVisualRoot,
      "r2-browser-e2e-report.json",
      ["r2-storyboard-derived-clip-result.png", "r2-removed-clip-zero-result.png"],
    ),
);
const outputPath = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_OUTPUT
    || join(
      repoRoot,
      "docs/SessionDesign-R2/OpenCrew_素材库综合分析_视频剪辑与跨页面语义检索_用户手册.html",
    ),
);

const setupReport = readJson(
  join(setupDir, "long-chinese-talking-head-report.json"),
);
const acceptanceReport = readJson(
  join(acceptanceDir, "visible-acceptance-report.json"),
);
const performanceReport = readJson(
  join(performanceDir, "dscf0157-performance-ui-report.json"),
);
const performanceUploadReport = readJson(
  join(performanceUploadDir, "dscf0157-performance-ui-report.json"),
);
const longWindowReport = readJson(join(longWindowDir, "report.json"));
const silentR1Report = readJson(join(silentR1Dir, "r1-browser-e2e-report.json"));
const silentR2Report = readJson(join(silentR2Dir, "r2-browser-e2e-report.json"));
assert.equal(setupReport.ok, true, "setup report must be successful");
assert.equal(acceptanceReport.ok, true, "visible acceptance report must be successful");
assert.equal(performanceReport.ok, true, "DSCF0157 performance report must be successful");
assert.equal(performanceUploadReport.ok, true, "DSCF0157 upload report must be successful");
assert.equal(longWindowReport.ok, true, "long-window regression report must be successful");
assert.equal(silentR1Report.ok, true, "silent visual R1 report must be successful");
assert.equal(silentR2Report.ok, true, "derived clip R2 report must be successful");

const main = setupReport.retained || {};
const retained = acceptanceReport.retained || {};
const checks = acceptanceReport.checks || {};
const screenshots = [];

function latestArtifact(root) {
  assert.ok(existsSync(root), `artifact root does not exist: ${root}`);
  const entries = readdirSync(root)
    .map((name) => join(root, name))
    .filter((path) => statSync(path).isDirectory())
    .sort((left, right) => basename(left).localeCompare(basename(right)));
  assert.ok(entries.length, `artifact root is empty: ${root}`);
  return entries.at(-1);
}

function latestArtifactWithFiles(root, reportName, requiredFiles) {
  assert.ok(existsSync(root), `artifact root does not exist: ${root}`);
  const entries = readdirSync(root)
    .map((name) => join(root, name))
    .filter((path) => statSync(path).isDirectory())
    .sort((left, right) => basename(right).localeCompare(basename(left)));
  const match = entries.find((dir) => {
    const reportPath = join(dir, reportName);
    if (!existsSync(reportPath) || requiredFiles.some((name) => !existsSync(join(dir, name)))) return false;
    try {
      return JSON.parse(readFileSync(reportPath, "utf8")).ok === true;
    } catch {
      return false;
    }
  });
  assert.ok(match, `no successful artifact with required files exists: ${root}`);
  return match;
}

function readJson(path) {
  assert.ok(existsSync(path), `report does not exist: ${path}`);
  return JSON.parse(readFileSync(path, "utf8"));
}

function workflowScreenshotCount(dir) {
  return readdirSync(dir).filter(
    (name) => name.toLowerCase().endsWith(".png") && !name.startsWith("99-"),
  ).length;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function imageData(path) {
  assert.ok(existsSync(path), `manual screenshot does not exist: ${path}`);
  return `data:image/png;base64,${readFileSync(path).toString("base64")}`;
}

function figure(dir, name, caption, note = "") {
  const path = join(dir, name);
  screenshots.push({ name, path, caption });
  return `<figure id="figure-${escapeHtml(name.replace(/\.png$/i, ""))}">
      <img src="${imageData(path)}" alt="${escapeHtml(caption)}" loading="lazy">
      <figcaption><strong>${escapeHtml(caption)}</strong>${note ? `<span>${escapeHtml(note)}</span>` : ""}</figcaption>
    </figure>`;
}

function metric(label, value, detail = "") {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</div>`;
}

const mainAssetId = main.asset_id || setupReport.asset_id;
const mainTaskId = main.task_id;
const mainSessionId = main.session_id;
const targetTaskId = acceptanceReport.task_id;
const targetSessionId = acceptanceReport.session_id;
const clip = retained.clip || {};
const internalSearch = checks.internal_semantic_search || {};
const externalSearch = checks.external_semantic_search || {};
const started = new Date(setupReport.started_at || Date.now());
const finished = new Date(acceptanceReport.finished_at || Date.now());
const generatedAt = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Australia/Sydney",
  dateStyle: "long",
  timeStyle: "medium",
}).format(new Date());
const setupScreenshotCount = workflowScreenshotCount(setupDir);
const acceptanceScreenshotCount = workflowScreenshotCount(acceptanceDir);
const performanceScreenshotCount = workflowScreenshotCount(performanceDir);
const longWindowScreenshotCount = workflowScreenshotCount(longWindowDir);
const silentR1ScreenshotCount = workflowScreenshotCount(silentR1Dir);
const silentR2ScreenshotCount = workflowScreenshotCount(silentR2Dir);
const dscfUpload = performanceUploadReport.upload || {};
const dscfApi = performanceReport.api || {};
const dscfRetainedUploads = performanceReport.retained_uploads || [];
const dscfSilentSearch = performanceReport.silent_search_boundary || {};

const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenCrew 素材库综合分析、视频剪辑与跨页面素材检索用户手册</title>
  <style>
    :root { color-scheme: light; --ink:#162033; --muted:#5f6f86; --line:#dfe6f1; --soft:#f5f8fc; --brand:#315ed5; --brand-soft:#edf3ff; --good:#12805c; --warn:#a66108; --code:#172033; }
    * { box-sizing:border-box; }
    html { scroll-behavior:smooth; }
    body { background:#eef2f7; color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.7; margin:0; }
    a { color:var(--brand); }
    .shell { background:#fff; box-shadow:0 18px 70px rgba(29,43,72,.12); margin:0 auto; max-width:1280px; min-height:100vh; }
    .hero { background:linear-gradient(135deg,#172b59,#315ed5 62%,#5787f4); color:#fff; padding:54px 64px 48px; }
    .eyebrow { font-size:12px; font-weight:700; letter-spacing:.16em; opacity:.78; text-transform:uppercase; }
    h1 { font-size:38px; letter-spacing:-.03em; line-height:1.2; margin:12px 0 16px; max-width:920px; }
    .hero p { font-size:17px; margin:0; max-width:900px; opacity:.9; }
    .hero .stamp { display:inline-flex; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.25); border-radius:999px; font-size:12px; margin-top:24px; padding:7px 12px; }
    .layout { display:grid; grid-template-columns:250px minmax(0,1fr); }
    nav { align-self:start; border-right:1px solid var(--line); height:100vh; overflow:auto; padding:30px 22px; position:sticky; top:0; }
    nav strong { display:block; font-size:12px; letter-spacing:.08em; margin-bottom:12px; text-transform:uppercase; }
    nav a { border-radius:8px; color:#46556d; display:block; font-size:13px; padding:7px 10px; text-decoration:none; }
    nav a:hover { background:var(--brand-soft); color:var(--brand); }
    main { min-width:0; padding:38px 54px 64px; }
    section { border-bottom:1px solid var(--line); padding:12px 0 44px; scroll-margin-top:20px; }
    section:last-child { border:0; }
    h2 { font-size:26px; letter-spacing:-.02em; margin:28px 0 12px; }
    h3 { font-size:18px; margin:28px 0 8px; }
    p { margin:8px 0 14px; }
    ul,ol { padding-left:24px; }
    li { margin:7px 0; }
    .lead { color:var(--muted); font-size:16px; }
    .callout { background:var(--brand-soft); border-left:4px solid var(--brand); border-radius:8px; margin:18px 0; padding:14px 18px; }
    .callout.warning { background:#fff7e8; border-color:#dc8c1c; }
    .callout.good { background:#ecfaf5; border-color:var(--good); }
    .metrics { display:grid; gap:12px; grid-template-columns:repeat(3,minmax(0,1fr)); margin:20px 0; }
    .metric { background:var(--soft); border:1px solid var(--line); border-radius:12px; min-width:0; padding:14px 16px; }
    .metric span,.metric small { color:var(--muted); display:block; font-size:11px; }
    .metric strong { display:block; font-size:17px; margin:3px 0; overflow-wrap:anywhere; }
    code { background:#eef2f8; border-radius:5px; color:var(--code); font-family:"SFMono-Regular",Consolas,monospace; font-size:.88em; overflow-wrap:anywhere; padding:2px 5px; word-break:break-word; }
    table { border-collapse:collapse; font-size:13px; margin:16px 0; width:100%; }
    th,td { border:1px solid var(--line); padding:10px 12px; text-align:left; vertical-align:top; }
    th { background:var(--soft); }
    figure { background:#f7f9fc; border:1px solid var(--line); border-radius:14px; margin:22px 0; overflow:hidden; }
    figure img { display:block; height:auto; max-width:100%; width:100%; }
    figcaption { color:#35445c; display:flex; flex-direction:column; font-size:13px; padding:12px 16px 14px; }
    figcaption span { color:var(--muted); font-size:12px; }
    .steps { counter-reset:manual-step; list-style:none; padding:0; }
    .steps > li { border-left:2px solid #cdd9f5; margin:0 0 0 14px; padding:0 0 22px 30px; position:relative; }
    .steps > li::before { align-items:center; background:var(--brand); border-radius:50%; color:#fff; content:counter(manual-step); counter-increment:manual-step; display:flex; font-size:12px; font-weight:700; height:28px; justify-content:center; left:-15px; position:absolute; top:0; width:28px; }
    .steps > li:last-child { border-left-color:transparent; }
    .status { border-radius:999px; display:inline-block; font-size:11px; font-weight:700; padding:2px 8px; }
    .status.good { background:#e5f7ef; color:var(--good); }
    .status.warn { background:#fff0d7; color:var(--warn); }
    .footer { background:#f6f8fb; color:var(--muted); font-size:12px; padding:22px 54px 34px; }
    @media (max-width:900px) { .hero { padding:38px 28px; } h1 { font-size:30px; } .layout { display:block; } nav { border-bottom:1px solid var(--line); border-right:0; height:auto; max-height:none; position:relative; } main { padding:24px 24px 50px; } .metrics { grid-template-columns:1fr 1fr; } }
    @media (max-width:560px) { .hero { padding:30px 20px; } h1 { font-size:26px; } nav { padding:20px 14px; } main { padding:20px 16px 40px; } .metrics { grid-template-columns:1fr; } table { table-layout:fixed; } th,td { overflow-wrap:anywhere; padding:8px; word-break:break-word; } .footer { padding:20px; } }
    @media print { body { background:#fff; } .shell { box-shadow:none; max-width:none; } nav { display:none; } .layout { display:block; } main { padding:20px 32px; } figure { break-inside:avoid; } .hero { print-color-adjust:exact; } }
  </style>
</head>
<body>
<div class="shell">
  <header class="hero">
    <div class="eyebrow">OpenCrew · Media Library</div>
    <h1>素材库综合分析、视频剪辑与跨页面素材检索用户手册</h1>
    <p>从真实中文口播视频上传开始，完成对白、画面结构、视觉语义、综合分析、跨素材检索、精确剪辑，并把原视频、外部素材和派生片段留存到 StoryBoard。</p>
    <p><strong>适用范围：</strong>M0–M4、R0A–R4 已验收链路。无声原视频按每个不超过 15 秒片段的 12.5% / 37.5% / 62.5% / 87.5% 固定采样四帧，一次多图分析后可按画面检索；成功剪切的派生片段可用人工名称和标签显式加入或移除全局素材检索。</p>
    <div class="stamp">独立单文件版 · 图片与样式均已内嵌 · ${escapeHtml(generatedAt)}</div>
  </header>
  <div class="layout">
    <nav aria-label="目录">
      <strong>目录</strong>
      <a href="#about">1. 本手册与验收素材</a>
      <a href="#entry">2. 进入素材库</a>
      <a href="#upload">3. 上传真实视频</a>
      <a href="#dialogue">4. 对白分析</a>
      <a href="#visual">5. 画面结构与视觉语义</a>
      <a href="#silent-visual">5A. 无声素材四帧视觉检索</a>
      <a href="#composite">6. 综合分析</a>
      <a href="#editor">7. 视频剪辑</a>
      <a href="#search">8. 中文对白/关键词检索</a>
      <a href="#external">9. 外部素材与 License</a>
      <a href="#clip">10. 创建并导入片段</a>
      <a href="#clip-search">10A. 派生片段全局复用</a>
      <a href="#storyboard">11. StoryBoard / Task / Session</a>
      <a href="#status">12. 状态、限制与排错</a>
      <a href="#evidence">13. 本轮真实验收记录</a>
    </nav>
    <main>
      <section id="about">
        <h2>1. 本手册与验收素材</h2>
        <p class="lead">本版手册不是静态界面示意。所有截图均来自本地测试环境的真实浏览器、真实 PostgreSQL 数据、真实视频文件、真实 FFmpeg 和真实分析运行。</p>
        <div class="metrics">
          ${metric("主素材", "中文真人口播 3:20", "Session #334 原始视频")}
          ${metric("对白证据", "96 段中文 ASR", "连续讲解黑谷 AI 拓客")}
          ${metric("场景证据", "12 shots / 24 scenes", "真人、后台、评论与微信演示")}
          ${metric("素材库标识", mainAssetId, `Task #${mainTaskId} / Session #${mainSessionId}`)}
          ${metric("导入目标", `Task #${targetTaskId}`, `Session #${targetSessionId}`)}
          ${metric("派生片段", clip.clip_id || "已创建", clip.display_name || "中文口播场景切换")}
        </div>
        <div class="callout warning"><strong>十分钟代表视频的用途：</strong>它只验证长时间轴、靠近尾部的非关键帧选区和 FFmpeg 技术路径，不用于证明对白识别、场景理解或中文检索质量。</div>
      </section>

      <section id="entry">
        <h2>2. 进入素材库</h2>
        <ol class="steps">
          <li>登录 OpenCrew 后，从左侧导航点击<strong>素材库</strong>。</li>
          <li>列表展示素材名称、时长、分辨率、对白摘要、分析状态、对白/视觉/综合片段数量、质量和标签。</li>
          <li>可使用顶部搜索框按素材名称、对白、标签或文件名筛选；点击素材名称进入详情，点击眼睛图标快速预览。</li>
        </ol>
        ${figure(setupDir, "01-media-library-before-upload.png", "素材库首页与上传入口")}
      </section>

      <section id="upload">
        <h2>3. 上传真实视频</h2>
        <ol class="steps">
          <li>点击右上角<strong>上传素材</strong>。</li>
          <li>拖入视频或点击选择文件。页面会显示文件名和大小；本次选择的是系统现有的 199.552 秒中文真人口播。</li>
          <li>点击<strong>上传</strong>。大文件会分片上传，完成后由服务端合并、ffprobe 探测，并创建独立素材 Task 与 Session。</li>
          <li>上传完成后回到列表，确认缩略图、3:20 时长、1200×2670 分辨率与“未分析”状态。</li>
        </ol>
        ${figure(setupDir, "02-real-chinese-video-selected.png", "选择 199 秒中文真人多场景口播")}
        ${figure(setupDir, "03-real-chinese-video-in-library.png", "上传完成：真实素材已留存在素材库", `素材 ${mainAssetId} · Task #${mainTaskId} / Session #${mainSessionId}`)}
        <h3>高码率相机原片</h3>
        <p><code>DSCF0157.MOV</code> 只有 26 秒，但原片为 335.6MB、1080p/50fps、约 103Mbps。系统保留原片用于分析和精确剪切，同时生成 1280 边界、30fps、fast-start 的 H.264 流畅预览；本轮代理为 ${(Number(dscfApi.preview_full?.bytes || 0) / 1024 / 1024).toFixed(2)}MB。</p>
        <div class="metrics">
          ${metric("原片", "335.6MB / 26 秒", "约 103Mbps")}
          ${metric("并发上传", `${dscfUpload.elapsed_ms || "-"}ms`, `${dscfUpload.chunk_request_count || "-"} 个分片`)}
          ${metric("预览代理", `${dscfApi.preview_full?.bytes || "-"} bytes`, "原片不被替换")}
        </div>
        ${figure(performanceUploadDir, "00-dscf0157-concurrent-upload-completed.png", "DSCF0157 并发上传完成并保留在素材库", `Asset ${dscfUpload.asset_id || performanceReport.asset_id} · Session #${dscfUpload.session_id || "-"}`)}
      </section>

      <section id="dialogue">
        <h2>4. 对白分析</h2>
        <ol class="steps">
          <li>打开素材详情，点击顶部<strong>对白分析</strong>工具集。</li>
          <li>如果配置的是云端 ASR，勾选“允许本次运行使用云端 ASR”。该授权只对本次 run 生效；本地 ASR 不会外发音频。</li>
          <li>点击运行图标。抽屉会显示准备独立分析环境、读取视频信息、语音识别（ASR）、对白与代表画面对齐等步骤。</li>
          <li>状态变为<strong>已完成</strong>后，在“对白分析”标签查看逐段时间码和真实中文转录；点击任一片段可在右侧视频定位播放。</li>
        </ol>
        ${figure(setupDir, "08-dialogue-analysis-ready.png", "对白分析完成：中文片段可查看、定位和播放")}
        <p><strong>“已完成”和“建议复核”不是矛盾状态：</strong>前者表示分析 run 已经结束并原子发布，后者表示 ASR 文本仍建议人工确认。本轮 74 秒中文口播的部分文字存在“化橘红/化血红”等识别差异，因此保留质量提醒，但不再写成容易误解为仍在运行的“待复核”。</p>
        ${figure(longWindowDir, "01-dialogue-complete-review-recommendation.png", "对白分析已完成，质量徽标明确显示为“建议复核”")}
        <h3>无音轨素材</h3>
        <p><code>DSCF0157.MOV</code> 经真实 ffprobe 与对白工具链确认没有音轨。此时页面显示“部分可用 / 无音轨”，不再把它误写成“等待授权”，也不会继续展示可误操作的 ASR 勾选。画面结构、视觉语义、预览和剪辑仍可正常使用。</p>
        <div class="callout warning"><strong>检索边界：</strong>无音轨不会覆盖素材真实聚合状态。只有当前有效的四帧 <code>visual_semantic</code> v2 才进入画面召回；历史单中点结果显示“需重新分析后可按画面检索”，不会伪装成四帧结果。本轮旧版边界检索 run <code>${escapeHtml(dscfSilentSearch.search_id || "-")}</code> 仍证明历史上传没有被错误纳入对白召回。</div>
        ${figure(performanceDir, "03-dscf0157-no-audio-business-state.png", "DSCF0157 无音轨业务状态与画面分析指引")}
        <table>
          <thead><tr><th>测试上传</th><th>素材 / Session</th><th>用途</th></tr></thead>
          <tbody>
            ${dscfRetainedUploads.map((entry, index) => `<tr><td>${["公网优化后", "本机优化后", "原始慢速基线"][index] || `测试 ${index + 1}`}</td><td><code>${escapeHtml(entry.asset_id)}</code> / Session #${escapeHtml(entry.session_id)}</td><td>${entry.analysis_status_reason === "video_has_no_audio" ? "无音轨状态与画面分析" : "上传和流畅预览性能"}</td></tr>`).join("")}
          </tbody>
        </table>
      </section>

      <section id="visual">
        <h2>5. 画面结构与视觉语义</h2>
        <p>画面分析分为两个阶段。新版结构分析把视频拆成不超过 15 秒的片段，并在每段的 12.5% / 37.5% / 62.5% / 87.5% 固定采样四帧；视觉语义对同一片段只发起一次包含四图的模型请求。四帧仍是稀疏证据，因此动作字段保持 <code>null</code>，不宣称连续动作理解。历史 <code>scene_midpoint_v1</code> 只读保留，选中的素材需按需重新分析。</p>
        <ol class="steps">
          <li>点击顶部<strong>画面分析</strong>，运行画面结构。等待“结构 已完成”。</li>
          <li>切到“画面分析”标签，确认场景检测与代表画面已发布。</li>
          <li>勾选“允许本次运行向已配置的云端视觉模型发送代表画面”。授权范围不包括源视频。</li>
          <li>点击<strong>运行视觉语义</strong>，完成后查看场景描述、代表画面、模型别名/版本和证据。</li>
        </ol>
        ${figure(setupDir, "09-visual-structure-ready.png", "画面结构已发布")}
        ${figure(setupDir, "11-visual-semantic-ready.png", "多场景视觉语义分析完成")}
        ${figure(longWindowDir, "02-five-visual-analysis-windows.png", "74 秒固定机位中文口播：5 个连续画面分析窗口")}
      </section>

      <section id="silent-visual">
        <h2>5A. 无声素材四帧视觉检索</h2>
        <p>本轮使用系统真实无声素材 <code>DSCF0157.MOV</code>（Asset <code>${escapeHtml(silentR1Report.asset_id)}</code> / Session #${escapeHtml(silentR1Report.asset_session_id)}）完成真实四帧结构与视觉语义分析。每个片段详情会展示四张真实图片、采样时间和证据；合格状态明确写“可按画面检索”。</p>
        <ol class="steps">
          <li>在素材列表确认无音轨素材不会覆盖画面分析聚合状态，并打开画面详情核对每段四帧。</li>
          <li>在 StoryBoard、Agent - Asset Library 或任意素材 editor 输入“玻璃碗”“深色液体”“绿色包装”等中文关键词。</li>
          <li>候选必须带 <code>visual_semantic</code> 命中片段、中文摘要和真实起止时间；从命中范围打开剪辑页后，默认选区等于命中范围。</li>
          <li>完成真实 FFmpeg 剪切并导入 StoryBoard，刷新后 Asset Pool 文件继续存在。</li>
        </ol>
        ${figure(silentR1Dir, "r1-asset-list-desktop.png", "无声素材列表：四帧视觉分析状态可见")}
        ${figure(silentR1Dir, "r1-four-frame-detail-desktop.png", "DSCF0157 四帧详情与真实证据", `Structure ${silentR1Report.visual_structure_run_id} · Semantic ${silentR1Report.visual_semantic_run_id}`)}
        ${figure(silentR1Dir, "r1-storyboard-visual-hit.png", "StoryBoard 命中真实视觉语义片段", `Search ${silentR1Report.storyboard_search_id}`)}
        ${figure(silentR1Dir, "r1-agent-visual-hit.png", "Agent - Asset Library 命中视觉片段", `Search ${silentR1Report.agent_media_library_search_id}`)}
        ${figure(silentR1Dir, "r1-editor-search-visual-hit.png", "editor 跨素材检索命中视觉片段", `Search ${silentR1Report.editor_search_id}`)}
        ${figure(silentR1Dir, "r1-editor-suggested-range.png", "从视觉命中打开 editor：建议选区等于 0–13000ms")}
        ${figure(silentR1Dir, "r1-real-cut-completed.png", "真实 DSCF0157 命中范围剪切完成", `Clip ${silentR1Report.clip_id}`)}
        ${figure(silentR1Dir, "r1-real-cut-imported.png", "真实视觉命中派生片段已导入 StoryBoard", `Import ${silentR1Report.import_id}`)}
        ${figure(silentR1Dir, "r1-asset-pool-import-retained.png", "刷新后 Asset Pool 保留视觉命中剪切文件")}
        ${figure(silentR1Dir, "r1-asset-list-mobile.png", "移动端无声素材列表无横向溢出")}
        ${figure(silentR1Dir, "r1-four-frame-detail-mobile.png", "移动端四帧证据完整可读")}
      </section>

      <section id="composite">
        <h2>6. 综合分析</h2>
        <p>综合分析只读取当前已发布的对白、画面结构和视觉语义结果，不再次读取源视频、音频或代表画面文件。</p>
        <ol class="steps">
          <li>切换到<strong>综合分析</strong>。只有对白、画面结构、视觉语义三项都“已完成”时，运行按钮才可用。</li>
          <li>点击<strong>运行综合分析</strong>，等待状态变为“已完成”。</li>
          <li>结果将对白边界和场景边界融合，供素材库搜索、StoryBoard 搜索和剪辑时间轴共用。</li>
        </ol>
        ${figure(setupDir, "13-composite-analysis-ready.png", "综合分析完成并发布片段")}
        ${figure(longWindowDir, "03-multiple-composite-segments.png", "同一 74 秒口播已发布 3 个综合语义片段，而非一个全片片段")}
      </section>

      <section id="editor">
        <h2>7. 视频剪辑</h2>
        <ol class="steps">
          <li>从素材详情点击<strong>视频剪辑</strong>。页面一次返回该素材全部 dialogue、visual、composite fragments，不分页、不静默截断。</li>
          <li>在时间轴查看源视频轨、对白轨、画面轨和综合轨；点击片段可将其时间范围设为当前选区。</li>
          <li>入点和出点首先显示易读时间码；需要精确输入时仍可填写<strong>入点毫秒</strong>与<strong>出点毫秒</strong>。点击“预览选区”可核对范围，缩放和滚动不会改变真实毫秒值。</li>
          <li>选择 StoryBoard 目标 Task，后续原视频、外部视频和派生片段都会导入该 Task。</li>
        </ol>
        ${figure(acceptanceDir, "06-editor-long-chinese-talking-head.png", "199 秒中文真人口播剪辑器：多轨片段与场景切换选区")}
        ${figure(performanceDir, "01-dscf0157-editor-light-desktop.png", "高码率 DSCF0157 浅色剪辑页与流畅预览")}
        ${figure(performanceDir, "02-dscf0157-editor-light-mobile.png", "高码率 DSCF0157 移动端剪辑布局与真实视频帧")}
        <div class="callout"><strong>长视频技术基线：</strong>下图用十分钟代表视频验证 543217–548217ms 的靠近尾部选区。它是时间轴与 FFmpeg 技术测试，不是语义质量样本。</div>
        ${figure(acceptanceDir, "06b-editor-ten-minute-tail-technical-baseline.png", "十分钟尾部非关键帧技术基线")}
      </section>

      <section id="search">
        <h2>8. 中文跨素材对白/关键词检索</h2>
        <div class="callout warning"><strong>当前能力边界：</strong>检索复用 Query Plan v1，并在同一候选模型中召回当前有效的对白片段、四帧视觉语义片段和显式开放的派生片段；不建设向量召回、clip 独立 VLM 或父视频视觉描述继承。</div>
        <ol class="steps">
          <li>在剪辑页点击<strong>素材检索</strong>。</li>
          <li>勾选“全局素材库”，输入对白原句、短语或关键词。本轮从“AI 拓客”主素材发起中文查询“中医 养肾 垫脚尖 真人口播 健康建议”。</li>
          <li>点击<strong>开始检索</strong>。系统会检索其他素材当前有效的已发布对白片段，综合结果可辅助构造查询条件；源素材自身会被排除。</li>
          <li>内部候选可以打开其剪辑页，也可点击<strong>导入原视频</strong>进入目标 StoryBoard。</li>
        </ol>
        ${figure(acceptanceDir, "07-editor-internal-semantic-search.png", "中文对白/关键词检索命中另一条真实素材", `${internalSearch.result_count || 0} 个结果`)}
        ${figure(acceptanceDir, "08-editor-original-imported.png", "素材库原视频已导入 StoryBoard")}
        <h3>StoryBoard 中的命中片段</h3>
        <p>StoryBoard 会按原视频归组，避免同一视频重复占满结果列表；这不表示只检索到整条视频。卡片内会逐条展示命中片段、精确时间范围和命中文本。点击<strong>剪切这个片段</strong>会把对应范围带入剪辑页；点击<strong>加入当前 Task（整条视频）</strong>才会导入整条原视频。</p>
        ${figure(longWindowDir, "04-storyboard-explicit-matched-fragments.png", "StoryBoard 结果按原视频归组，同时展示 3 个可操作命中片段", `Search ${longWindowReport.search?.search_id || "-"}`)}
        ${figure(longWindowDir, "05-matched-fragment-opened-in-editor.png", "从命中片段进入剪辑页，精确起止时间已自动带入")}
      </section>

      <section id="external">
        <h2>9. 外部素材与 License</h2>
        <ol class="steps">
          <li>在对白/关键词检索面板勾选<strong>外部素材</strong>，运行检索。</li>
          <li>每个外部候选会显示 Provider、Creator 和 License。外部候选不会出现“打开其剪辑页”。</li>
          <li>导入按钮默认禁用。阅读元数据后勾选“我已阅读并确认 License”，受支持的候选才可整条导入。</li>
          <li>外部提供商不支持下载或返回不完整授权信息时，按钮会保持禁用，不会静默降级。</li>
        </ol>
        ${figure(acceptanceDir, "09-editor-external-semantic-search.png", "真实外部 Provider、Creator 与 License 元数据", `${externalSearch.result_count || 0} 个结果`)}
        ${figure(acceptanceDir, "10-editor-external-imported.png", "显式确认 License 后导入外部视频")}
      </section>

      <section id="clip">
        <h2>10. 创建并导入派生片段</h2>
        <ol class="steps">
          <li>确认入点、出点和片段名称后，点击<strong>创建剪切任务</strong>。</li>
          <li>后台使用真实 FFmpeg 输入侧单 <code>-ss</code> 与 accurate seek 生成视频；任务状态会从 queued/running 进入 completed，或显示可见失败/取消状态。</li>
          <li>打开“派生片段”，可以预览、下载或删除结果。点击<strong>导入 StoryBoard</strong>将派生片段写入当前目标 Task。</li>
          <li>导入不会删除素材库中的派生片段；刷新页面后 clip 与导入记录仍会保留。</li>
        </ol>
        ${figure(acceptanceDir, "11-editor-retained-clip-completed.png", "真实 FFmpeg 中文口播片段剪切完成", `${clip.start_ms || "-"}–${clip.end_ms || "-"}ms · ${clip.clip_id || "-"}`)}
        ${figure(acceptanceDir, "12-editor-retained-clip-imported.png", "派生片段已导入 StoryBoard 并继续留存在剪辑页")}
      </section>

      <section id="clip-search">
        <h2>10A. 派生片段加入全局素材检索</h2>
        <p>只有剪辑成功的派生片段卡提供这项能力。首版只按人工名称和标签检索，不继承父视频视觉描述。当前为部署级全局可见，因此按钮明确写“加入全局素材检索”，不会在创建 clip 时自动发布。</p>
        <ol class="steps">
          <li>打开源素材的“派生片段”，填写名称和标签；名称最多 255 字符，标签按固定数量与长度合同校验。</li>
          <li>点击<strong>加入全局素材检索</strong>。刷新页面后状态和元数据必须仍然存在。</li>
          <li>在另一个 Task 的 StoryBoard、Agent - Asset Library 和任意素材 editor 按名称/标签搜索。候选身份显示“可复用片段”，本地时间为 0–片段时长，来源时间保留父视频精确范围。</li>
          <li>点击预览使用派生片段自己的受控文件；导入动作固定为 <code>import_clip</code>，不会混淆为原视频整条导入或打开父素材剪辑页。</li>
          <li>点击<strong>移除全局素材检索</strong>后，旧 replay 和新搜索都不再返回；已经导入 StoryBoard 的文件继续保留。</li>
        </ol>
        ${figure(silentR2Dir, "r2-clip-join-metadata.png", "派生片段填写真实名称与标签", `Clip ${silentR2Report.clip_id}`)}
        ${figure(silentR2Dir, "r2-clip-global-search-enabled.png", "派生片段已加入全局素材检索")}
        ${figure(silentR2Dir, "r2-storyboard-derived-clip-result.png", "StoryBoard 返回可复用派生片段", `Search ${silentR2Report.storyboard_search_id}`)}
        ${figure(silentR2Dir, "r2-storyboard-clip-exact-preview.png", "StoryBoard 精确预览派生片段自身文件")}
        ${figure(silentR2Dir, "r2-storyboard-clip-imported.png", "StoryBoard 使用 import_clip 导入派生片段")}
        ${figure(silentR2Dir, "r2-asset-pool-import-retained.png", "刷新后 Asset Pool 保留派生片段导入")}
        ${figure(silentR2Dir, "r2-agent-derived-clip-result.png", "Agent - Asset Library 返回派生片段", `Search ${silentR2Report.agent_media_library_search_id}`)}
        ${figure(silentR2Dir, "r2-agent-clip-imported.png", "Agent 精确导入派生片段")}
        ${figure(silentR2Dir, "r2-editor-derived-clip-result.png", "editor 返回派生片段且不显示原视频剪辑动作", `Search ${silentR2Report.editor_search_id}`)}
        ${figure(silentR2Dir, "r2-editor-clip-imported.png", "editor 通过统一路由导入派生片段")}
        ${figure(silentR2Dir, "r2-storyboard-derived-clip-mobile.png", "移动端 StoryBoard 素材抽屉可检索派生片段")}
        ${figure(silentR2Dir, "r2-clip-global-search-mobile.png", "移动端派生片段全局检索状态完整可读")}
        ${figure(silentR2Dir, "r2-clip-global-search-removed.png", "派生片段已显式移除全局素材检索")}
        ${figure(silentR2Dir, "r2-removed-clip-zero-result.png", "移除后新搜索为零结果", `Search ${silentR2Report.removal_search_id}`)}
        ${figure(silentR2Dir, "r2-import-retained-after-removal.png", "移除检索后已导入文件仍保留")}
      </section>

      <section id="storyboard">
        <h2>11. StoryBoard、Task 与 Session 留存</h2>
        <ol class="steps">
          <li>从剪辑页导入后，进入<strong>故事版（口播）</strong>并打开目标 Task。</li>
          <li>在右侧素材池点击“上传素材”，确认原视频、外部视频和派生片段卡片均可见。</li>
          <li>返回任务列表，搜索 Task ID，确认 Task、Session 和视频计数持续可见。</li>
        </ol>
        ${figure(acceptanceDir, "13-storyboard-retained-assets.png", "StoryBoard 素材池：原视频、外部视频和派生片段全部留存")}
        ${figure(acceptanceDir, "14-task-session-with-retained-assets.png", `任务列表：Task #${targetTaskId} / Session #${targetSessionId} 与留存计数`)}
        <table>
          <thead><tr><th>对象</th><th>本轮标识</th><th>用途</th></tr></thead>
          <tbody>
            <tr><td>上传素材</td><td><code>${escapeHtml(mainAssetId)}</code></td><td>199 秒中文真人多场景口播</td></tr>
            <tr><td>素材 Task / Session</td><td>Task #${escapeHtml(mainTaskId)} / Session #${escapeHtml(mainSessionId)}</td><td>每条上传素材的独立分析工作区与运行快照</td></tr>
            <tr><td>StoryBoard 目标</td><td>Task #${escapeHtml(targetTaskId)} / Session #${escapeHtml(targetSessionId)}</td><td>留存检索候选、外部视频和派生片段</td></tr>
            <tr><td>派生片段</td><td><code>${escapeHtml(clip.clip_id || "-")}</code></td><td>${escapeHtml(clip.display_name || "中文口播场景切换")}</td></tr>
          </tbody>
        </table>
      </section>

      <section id="status">
        <h2>12. 状态、限制与排错</h2>
        <table>
          <thead><tr><th>状态/现象</th><th>含义</th><th>处理</th></tr></thead>
          <tbody>
            <tr><td><span class="status warn">等待中 / 运行中</span></td><td>真实工具或模型仍在执行</td><td>保持页面打开或稍后刷新；Task/Session 与 run 已持久化。</td></tr>
            <tr><td><span class="status warn">等待授权</span></td><td>云端 ASR/视觉模型需要本次显式授权，或配置不可用</td><td>阅读授权范围后重新运行；不可用时页面会显示结构化原因，不会伪造结果。</td></tr>
            <tr><td><span class="status warn">无音轨 · 可画面分析</span></td><td>视频确实没有音频轨，不是授权勾选失效</td><td>无需重复勾选 ASR；继续四帧画面分析、预览和剪辑。合格 visual semantic 发布后可按画面召回，但仍不进入对白召回。</td></tr>
            <tr><td><span class="status warn">需重新分析后可按画面检索</span></td><td>只有历史单中点 <code>scene_midpoint_v1</code> 结果</td><td>按需重新运行四帧结构与视觉语义分析；系统不会自动重跑全库。</td></tr>
            <tr><td><span class="status good">可按画面检索</span></td><td>当前 source_version 已有完整四帧 <code>visual_semantic</code> v2</td><td>可从 StoryBoard、Agent 或 editor 输入中文画面关键词。</td></tr>
            <tr><td><span class="status good">已加入全局素材检索</span></td><td>派生片段名称/标签已显式发布</td><td>部署内其他 Task 可搜索、精确预览并导入；移除不影响既有导入文件。</td></tr>
            <tr><td><span class="status good">已完成</span></td><td>结果已原子发布并成为 current</td><td>可用于检索、综合分析与剪辑。</td></tr>
            <tr><td><span class="status warn">建议复核</span></td><td>run 已完成，但该片段的识别文本或综合质量建议人工确认</td><td>可继续查看、检索和剪辑；重要文案使用前核对原视频，不要把它理解为仍在运行。</td></tr>
            <tr><td>已过期 stale</td><td>上游 source_version 或依赖 run 已变化</td><td>重新运行相应分析；编辑器会阻止把 stale fragment 当作可信来源，或允许转为手动范围。</td></tr>
            <tr><td>外部导入按钮禁用</td><td>尚未确认 License，或 Provider 不支持真实下载</td><td>先确认 License；不支持时更换候选。</td></tr>
            <tr><td>素材规模超过试运行边界</td><td>500 条是首版简单检索的性能边界，不是磁盘容量限制</td><td>继续保留素材；检索时使用更接近对白原句的短语和关键词缩小范围。</td></tr>
            <tr><td>高码率 MOV 播放反复缓冲</td><td>相机原片可能超过浏览器和公网持续直播放宽</td><td>新上传会自动生成带版本缓存和 Range 支持的流畅预览；分析与剪切始终使用未改动原片。</td></tr>
            <tr><td>HEVC 视频在 Chromium 无画面</td><td>Chrome/Chromium 的 HTML5 编解码能力不保证支持 HEVC；本轮 3:20 主素材在 macOS WebKit/Safari 中可正常解码</td><td>使用 Safari/WebKit 播放 HEVC；需要跨浏览器直放时优先上传 H.264/AAC MP4。</td></tr>
          </tbody>
        </table>
        <div class="callout good"><strong>数据保留：</strong>本轮测试没有删除上传素材、分析 run、Task/Session、检索 run、外部导入、派生 clip 或 StoryBoard 导入记录，刷新页面后仍可查看。</div>
      </section>

      <section id="evidence">
        <h2>13. 本轮真实验收记录</h2>
        <div class="metrics">
          ${metric("上传/分析报告", basename(setupDir), `${setupScreenshotCount} 张流程截图`)}
          ${metric("综合 UI 报告", basename(acceptanceDir), `${acceptanceScreenshotCount} 张流程截图`)}
          ${metric("高码率性能报告", basename(performanceDir), `${performanceScreenshotCount} 张专项截图`)}
          ${metric("长镜头回归报告", basename(longWindowDir), `${longWindowScreenshotCount} 张专项截图`)}
          ${metric("R1 无声视觉检索", basename(silentR1Dir), `${silentR1ScreenshotCount} 张真实截图`)}
          ${metric("R2 派生片段复用", basename(silentR2Dir), `${silentR2ScreenshotCount} 张真实截图`)}
          ${metric("浏览器引擎", acceptanceReport.browser_engine || "webkit", "真实浏览器 E2E")}
          ${metric("浏览器异常", String((setupReport.page_errors?.length || 0) + (acceptanceReport.page_errors?.length || 0) + (longWindowReport.page_errors?.length || 0)), "pageerror")}
          ${metric("HTTP 5xx", String((setupReport.api_failures?.length || 0) + (acceptanceReport.api_failures?.length || 0) + (longWindowReport.api_failures?.length || 0)), "验收期间")}
          ${metric("开始时间", started.toLocaleString("zh-CN", { timeZone:"Australia/Sydney" }), "Australia/Sydney")}
          ${metric("完成时间", finished.toLocaleString("zh-CN", { timeZone:"Australia/Sydney" }), "Australia/Sydney")}
        </div>
        <h3>移动端浏览器验证</h3>
        <p>以下截图使用 390×844 真实浏览器视口重新打开素材库、分析详情和剪辑器，验证窄屏下的导航、信息区和编辑操作仍可访问。</p>
        ${figure(acceptanceDir, "15-mobile-media-library.png", "移动端素材库列表")}
        ${figure(acceptanceDir, "16-mobile-analysis-detail.png", "移动端分析详情")}
        ${figure(acceptanceDir, "17-mobile-editor.png", "移动端视频剪辑器")}
        ${figure(acceptanceDir, "17a-mobile-editor-semantic-search.png", "移动端剪辑器对白/关键词检索面板")}
        ${figure(acceptanceDir, "17b-mobile-editor-timeline.png", "移动端剪辑器时间轴")}
        <p>报告文件：<code>${escapeHtml(join(setupDir, "long-chinese-talking-head-report.json"))}</code>、<code>${escapeHtml(join(acceptanceDir, "visible-acceptance-report.json"))}</code>、<code>${escapeHtml(join(performanceDir, "dscf0157-performance-ui-report.json"))}</code>、<code>${escapeHtml(join(longWindowDir, "report.json"))}</code>、<code>${escapeHtml(join(silentR1Dir, "r1-browser-e2e-report.json"))}</code> 与 <code>${escapeHtml(join(silentR2Dir, "r2-browser-e2e-report.json"))}</code>。</p>
        <p>本 HTML 共内嵌 ${screenshots.length} 张产品截图。打开本文件不依赖截图目录、Web 服务器、网络或相对路径。</p>
      </section>
    </main>
  </div>
  <footer class="footer">OpenCrew 素材库用户手册 · 单文件离线版 · 所有截图和样式均内嵌于本 HTML。</footer>
</div>
</body>
</html>`;

writeFileSync(outputPath, html);
console.log(JSON.stringify({
  ok: true,
  output_path: outputPath,
  size_bytes: Buffer.byteLength(html),
  embedded_screenshot_count: screenshots.length,
  setup_artifact_dir: setupDir,
  acceptance_artifact_dir: acceptanceDir,
  performance_artifact_dir: performanceDir,
  performance_upload_artifact_dir: performanceUploadDir,
  long_window_artifact_dir: longWindowDir,
  silent_visual_r1_artifact_dir: silentR1Dir,
  silent_visual_r2_artifact_dir: silentR2Dir,
}, null, 2));
