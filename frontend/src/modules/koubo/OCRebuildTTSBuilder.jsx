import { For, Show, createEffect, createSignal, onCleanup, onMount } from "solid-js";
import { getSharedAudioContext } from "./shared/audioContext.js";

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function roundTime(value) {
  return Math.round(value * 100) / 100;
}

function ReferenceWaveformSelector(props) {
  let canvasEl;
  let trackEl;
  let resizeObserver;
  let activeDrag = null;
  let capturedPointerId = null;
  let dragIdleTimer = null;
  let loadedAudioUrl = "";
  const [peaks, setPeaks] = createSignal([]);
  const [audioDuration, setAudioDuration] = createSignal(0);
  const [trackWidth, setTrackWidth] = createSignal(1);
  const [phase, setPhase] = createSignal("idle");
  const [dragSelection, setDragSelection] = createSignal(null);
  const savedStart = () => clamp(Number(props.workflow?.voiceReferenceStart || 0), 0, Math.max(0, audioDuration()));
  const savedEnd = () => {
    const duration = Math.max(0.1, Number(props.workflow?.voiceReferenceDuration || 0.1));
    const maxEnd = audioDuration() || (savedStart() + duration);
    return clamp(savedStart() + duration, Math.min(0.1, maxEnd), maxEnd);
  };
  const viewStart = () => dragSelection()?.start ?? savedStart();
  const viewEnd = () => dragSelection()?.end ?? savedEnd();
  const selectedDuration = () => Math.max(0.1, viewEnd() - viewStart());
  const pct = (time) => audioDuration() > 0 ? `${clamp(time / audioDuration(), 0, 1) * 100}%` : "0%";
  const timeLabel = () => audioDuration() ? `${viewStart().toFixed(2)}s - ${viewEnd().toFixed(2)}s / ${selectedDuration().toFixed(2)}s` : "--";
  const sanitizeSelection = (nextStart, nextEnd) => {
    const max = audioDuration();
    if (!max) return null;
    const cleanStart = roundTime(clamp(nextStart, 0, Math.max(0, max - 0.1)));
    const cleanEnd = roundTime(clamp(nextEnd, cleanStart + 0.1, max));
    return { start: cleanStart, end: cleanEnd };
  };
  const commitSelection = (nextStart, nextEnd) => {
    const clean = sanitizeSelection(nextStart, nextEnd);
    if (!clean) return;
    props.onChange?.(props.workflow.workflowId, {
      voiceReferenceStart: clean.start,
      voiceReferenceDuration: roundTime(clean.end - clean.start),
    });
  };
  const previewSelection = (nextStart, nextEnd) => {
    const clean = sanitizeSelection(nextStart, nextEnd);
    if (!clean) return;
    setDragSelection(clean);
  };
  const timeFromPointer = (event) => {
    const rect = trackEl?.getBoundingClientRect();
    if (!rect || !audioDuration()) return 0;
    return clamp(((event.clientX - rect.left) / Math.max(1, rect.width)) * audioDuration(), 0, audioDuration());
  };
  const dragModeFromPointer = (event) => {
    const rect = trackEl?.getBoundingClientRect();
    if (!rect || !audioDuration()) return "selection";
    const x = event.clientX - rect.left;
    const startX = (viewStart() / audioDuration()) * rect.width;
    const endX = (viewEnd() / audioDuration()) * rect.width;
    const edgeHit = Math.max(18, Math.min(32, rect.width * 0.035));
    if (Math.abs(x - startX) <= edgeHit) return "left";
    if (Math.abs(x - endX) <= edgeHit) return "right";
    return "selection";
  };
  const draw = () => {
    if (!canvasEl) return;
    const width = Math.max(1, Math.floor(trackWidth()));
    const height = 56;
    const dpr = window.devicePixelRatio || 1;
    canvasEl.width = Math.floor(width * dpr);
    canvasEl.height = Math.floor(height * dpr);
    canvasEl.style.width = `${width}px`;
    canvasEl.style.height = `${height}px`;
    const ctx = canvasEl.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#f8fafc";
    ctx.fillRect(0, 0, width, height);
    const data = peaks();
    const mid = height / 2;
    ctx.fillStyle = "#cbd5e1";
    ctx.fillRect(0, mid - 0.5, width, 1);
    if (!data.length) return;
    const selectedLeft = audioDuration() ? (viewStart() / audioDuration()) * width : 0;
    const selectedRight = audioDuration() ? (viewEnd() / audioDuration()) * width : 0;
    const barWidth = Math.max(1, width / data.length);
    data.forEach((peak, index) => {
      const x = index * barWidth;
      const barHeight = Math.max(2, peak * (height - 12));
      ctx.fillStyle = x >= selectedLeft && x <= selectedRight ? "#2563eb" : "#94a3b8";
      ctx.fillRect(x, mid - barHeight / 2, Math.max(1, barWidth - 1), barHeight);
    });
  };

  createEffect(() => {
    const url = props.audioUrl;
    let canceled = false;
    if (url && url === loadedAudioUrl && (phase() === "ready" || phase() === "loading")) return;
    loadedAudioUrl = url || "";
    setPeaks([]);
    setAudioDuration(0);
    setPhase(url ? "loading" : "idle");
    if (!url) return;
    const audioContext = getSharedAudioContext();
    if (!audioContext) {
      setPhase("unsupported");
      return;
    }
    fetch(url, { credentials: "include" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.arrayBuffer();
      })
      .then((buffer) => audioContext.decodeAudioData(buffer.slice(0)))
      .then((decoded) => {
        if (canceled) return;
        const channel = decoded.getChannelData(0);
        const bins = 220;
        const samplesPerBin = Math.max(1, Math.floor(channel.length / bins));
        const nextPeaks = Array.from({ length: bins }, (_, bin) => {
          const startIndex = bin * samplesPerBin;
          const endIndex = Math.min(channel.length, startIndex + samplesPerBin);
          let max = 0;
          for (let i = startIndex; i < endIndex; i += 1) max = Math.max(max, Math.abs(channel[i]));
          return Math.min(1, max);
        });
        setAudioDuration(decoded.duration || 0);
        setPeaks(nextPeaks);
        setPhase("ready");
        const currentStart = Number(props.workflow?.voiceReferenceStart || 0);
        const currentEnd = currentStart + Math.max(0.1, Number(props.workflow?.voiceReferenceDuration || 0.1));
        if (decoded.duration > 0 && currentEnd > decoded.duration) commitSelection(Math.min(currentStart, Math.max(0, decoded.duration - 0.1)), decoded.duration);
      })
      .catch(() => {
        if (!canceled) setPhase("error");
      });
    onCleanup(() => { canceled = true; });
  });

  createEffect(() => {
    peaks();
    viewStart();
    viewEnd();
    trackWidth();
    draw();
  });

  onMount(() => {
    const updateWidth = () => setTrackWidth(Math.max(1, Math.floor(trackEl?.getBoundingClientRect().width || 1)));
    updateWidth();
    resizeObserver = new ResizeObserver(updateWidth);
    if (trackEl) resizeObserver.observe(trackEl);
  });
  onCleanup(() => {
    resizeObserver?.disconnect();
    stopDragListeners();
    clearDraggingClass();
  });

  function startDragListeners() {
    document.addEventListener("pointermove", onPointerMove, true);
    document.addEventListener("mousemove", onPointerMove, true);
    document.addEventListener("pointerup", finishDrag, true);
    document.addEventListener("pointercancel", finishDrag, true);
    document.addEventListener("mouseup", finishDrag, true);
    document.addEventListener("dragend", finishDrag, true);
    document.addEventListener("contextmenu", finishDrag, true);
    document.addEventListener("visibilitychange", finishDrag, true);
    window.addEventListener("blur", finishDrag);
    window.addEventListener("mouseup", finishDrag, true);
    trackEl?.addEventListener("lostpointercapture", finishDrag);
  }
  function stopDragListeners() {
    if (dragIdleTimer) {
      window.clearTimeout(dragIdleTimer);
      dragIdleTimer = null;
    }
    document.removeEventListener("pointermove", onPointerMove, true);
    document.removeEventListener("mousemove", onPointerMove, true);
    document.removeEventListener("pointerup", finishDrag, true);
    document.removeEventListener("pointercancel", finishDrag, true);
    document.removeEventListener("mouseup", finishDrag, true);
    document.removeEventListener("dragend", finishDrag, true);
    document.removeEventListener("contextmenu", finishDrag, true);
    document.removeEventListener("visibilitychange", finishDrag, true);
    window.removeEventListener("blur", finishDrag);
    window.removeEventListener("mouseup", finishDrag, true);
    trackEl?.removeEventListener("lostpointercapture", finishDrag);
    if (capturedPointerId !== null) {
      try {
        trackEl?.releasePointerCapture?.(capturedPointerId);
      } catch {
        // Pointer capture may already be released by the browser.
      }
      capturedPointerId = null;
    }
  }
  function setDraggingClass() {
    document.body.classList.add("is-dragging-waveform-range");
    document.documentElement.classList.add("is-dragging-waveform-range");
  }
  function clearDraggingClass() {
    document.body.classList.remove("is-dragging-waveform-range");
    document.documentElement.classList.remove("is-dragging-waveform-range");
  }
  function finishDrag() {
    const current = dragSelection();
    if (current) commitSelection(current.start, current.end);
    setDragSelection(null);
    activeDrag = null;
    clearDraggingClass();
    stopDragListeners();
  }
  function scheduleIdleRelease() {
    if (dragIdleTimer) window.clearTimeout(dragIdleTimer);
    dragIdleTimer = window.setTimeout(() => {
      finishDrag();
    }, 900);
  }

  function startDrag(mode, event) {
    if (!audioDuration()) return;
    event.preventDefault();
    event.stopPropagation();
    capturedPointerId = event.pointerId;
    try {
      trackEl?.setPointerCapture?.(event.pointerId);
    } catch {
      capturedPointerId = null;
    }
    activeDrag = { mode, x: event.clientX, start: viewStart(), end: viewEnd(), span: selectedDuration() };
    previewSelection(viewStart(), viewEnd());
    setDraggingClass();
    startDragListeners();
    scheduleIdleRelease();
  }
  function startDragFromSelection(event) {
    startDrag(dragModeFromPointer(event), event);
  }
  function onPointerMove(event) {
    if (!activeDrag || !audioDuration()) return;
    if (event.buttons !== undefined && (event.buttons & 1) !== 1) {
      finishDrag();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const rect = trackEl?.getBoundingClientRect();
    const delta = ((event.clientX - activeDrag.x) / Math.max(1, rect?.width || 1)) * audioDuration();
    if (activeDrag.mode === "left") previewSelection(activeDrag.start + delta, activeDrag.end);
    if (activeDrag.mode === "right") previewSelection(activeDrag.start, activeDrag.end + delta);
    if (activeDrag.mode === "selection") {
      const nextStart = clamp(activeDrag.start + delta, 0, Math.max(0, audioDuration() - activeDrag.span));
      previewSelection(nextStart, nextStart + activeDrag.span);
    }
    scheduleIdleRelease();
  }
  function onTrackPointerDown(event) {
    if (!audioDuration() || !trackEl?.contains(event.target)) return;
    const edgeMode = dragModeFromPointer(event);
    if (edgeMode !== "selection") {
      startDrag(edgeMode, event);
      return;
    }
    if (event.target.closest?.(".ocrebuild-reference-waveform-selection, .ocrebuild-reference-waveform-line")) return;
    event.preventDefault();
    event.stopPropagation();
    const span = selectedDuration();
    const nextStart = clamp(timeFromPointer(event) - span / 2, 0, Math.max(0, audioDuration() - span));
    previewSelection(nextStart, nextStart + span);
    activeDrag = { mode: "selection", x: event.clientX, start: nextStart, end: nextStart + span, span };
    setDraggingClass();
    capturedPointerId = event.pointerId;
    try {
      trackEl?.setPointerCapture?.(event.pointerId);
    } catch {
      capturedPointerId = null;
    }
    startDragListeners();
    scheduleIdleRelease();
  }

  return <div class={`ocrebuild-reference-waveform ${phase()}`} data-phase={phase()}>
    <div class="ocrebuild-reference-waveform-meta">
      <span>{phase() === "loading" ? "载入波形" : phase() === "error" ? "波形读取失败" : "选择片段"}</span>
      <strong>{timeLabel()}</strong>
      <em>{audioDuration() ? `共 ${audioDuration().toFixed(2)}s` : ""}</em>
    </div>
    <div class="ocrebuild-reference-waveform-track" ref={trackEl} onPointerDown={onTrackPointerDown}>
      <canvas ref={canvasEl} />
      <Show when={audioDuration()}>
        <>
          <div class="ocrebuild-reference-waveform-selection" style={{ left: pct(viewStart()), width: `calc(${pct(viewEnd())} - ${pct(viewStart())})` }} onPointerDown={startDragFromSelection} />
          <span class="ocrebuild-reference-waveform-playhead" style={{ left: pct(viewStart()) }} aria-hidden="true" />
          <span class="ocrebuild-reference-waveform-line is-left" style={{ left: pct(viewStart()) }} role="slider" tabindex="0" title="调整开始时间" aria-label="调整开始时间" aria-valuemin="0" aria-valuemax={audioDuration().toFixed(2)} aria-valuenow={viewStart().toFixed(2)} onPointerDown={(event) => startDrag("left", event)} />
          <span class="ocrebuild-reference-waveform-line is-right" style={{ left: pct(viewEnd()) }} role="slider" tabindex="0" title="调整结束时间" aria-label="调整结束时间" aria-valuemin="0" aria-valuemax={audioDuration().toFixed(2)} aria-valuenow={viewEnd().toFixed(2)} onPointerDown={(event) => startDrag("right", event)} />
        </>
      </Show>
    </div>
  </div>;
}

export default function OCRebuildTTSBuilder(props) {
  const {
    CloseIcon,
    CopyIcon,
    DocumentIcon,
    PauseIcon,
    PlayIcon,
    SaveIcon,
    SpeakerIcon,
    UploadIcon,
  } = props.icons;

  const wf = () => props.workflow;
  const pos = () => props.position?.();
  const referencePath = () => props.voiceReferenceDisplayPath(wf());
  const referenceName = () => {
    const path = referencePath();
    return path ? path.split(/[\\/]/).filter(Boolean).pop() || path : "未选择参考声音";
  };
  const referenceAudioUrl = () => props.voiceReferenceAudioUrl(wf());
  const referencePlaying = () => wf().voicePlayback?.key === props.voiceReferencePlaybackKey(wf());
  const copyReferencePath = async () => {
    const path = referencePath();
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = path;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
  };

  const renderVoiceRecommendationRow = (item, index) => {
    const workflow = wf();
    const parts = item.score_parts || {};
    const key = props.voiceRecommendationKey(item);
    const selected = workflow.voiceSelection?.candidate_id ? workflow.voiceSelection?.candidate_id === item.candidate_id : (workflow.voiceSelection?.voice_id === item.voice_id && workflow.voiceSelection?.provider === item.provider && workflow.voiceSelection?.model === item.model);
    const guideActive = props.voiceGuideDialog()?.key === key;
    const isPlaying = workflow.voicePlayback?.key === key;
    const infoOpen = workflow.voiceInfoOpenKey === key;
    const promptTemplate = String(item.prompt_template || item.instructions || "").trim();
    const rawPrompt = String(item.prompt || "").trim();
    const referenceText = String(workflow.voiceReferenceText || workflow.voiceRecommendationResult?.sample_text || "").trim();
    const expandedPrompt = referenceText && promptTemplate ? promptTemplate.replaceAll("{text}", referenceText) : promptTemplate;
    const promptText = promptTemplate === "{text}" ? (referenceText ? `正文：${referenceText}` : "仅朗读正文 / 无额外提示词") : (expandedPrompt || rawPrompt);
    const scoreParts = [
      ["音色", parts.timbre],
      ["音调", parts.pitch],
      ["亮度", parts.brightness],
      ["能量", parts.energy],
      ["节奏", parts.rhythm],
      ["时长", parts.duration],
      ["表现力", parts.expressiveness],
    ].filter(([, value]) => value !== undefined && value !== null);
    return <div class={`ocrebuild-voice-rec-row ${selected ? "is-selected" : ""} ${guideActive ? "is-guide-active" : ""}`}>
      <div class="ocrebuild-voice-rec-rank">{index() + 1}</div>
      <div class="ocrebuild-voice-name"><strong>{item.label || item.voice_id}</strong></div>
      <p class="ocrebuild-voice-prompt" title={promptText || "No instruct"}>{promptText || "No instruct / 仅按正文朗读"}</p>
      <button class={`icon-action ocrebuild-voice-action ${isPlaying ? "is-playing" : ""}`} type="button" title={isPlaying ? "停止" : "播放"} aria-label={`${isPlaying ? "停止" : "播放"} ${item.label || item.voice_id}`} onClick={() => props.toggleVoiceRecommendationPlayback(workflow.workflowId, item)}>{isPlaying ? <PauseIcon /> : <SpeakerIcon />}</button>
      <button class="icon-action ocrebuild-voice-action is-primary" type="button" title="选用" aria-label={`选用 ${item.label || item.voice_id}`} onClick={() => void props.selectRecommendedVoice(workflow.workflowId, item)}><SaveIcon /></button>
      <button class="icon-action ocrebuild-voice-action" type="button" title="测试" aria-label={`测试 ${item.label || item.voice_id}`} onClick={() => props.openVoiceGuide(workflow.workflowId, item)}><DocumentIcon /></button>
      <button class={`icon-action ocrebuild-voice-action ocrebuild-voice-info-button ${infoOpen ? "is-open" : ""}`} type="button" title="评分" aria-label={`评分 ${item.label || item.voice_id}`} aria-expanded={infoOpen} onClick={() => props.toggleVoiceRecommendationInfo(workflow.workflowId, item)}>i</button>
      <Show when={infoOpen}>
        <div class="ocrebuild-voice-eval-detail">
          <div><strong>总分 {Number(item.score || 0).toFixed(4)}</strong><span>{item.candidate_id || "candidate"}{item.raw_duration ? ` · raw ${Number(item.raw_duration).toFixed(2)}s` : ""}{item.fit_duration ? ` · fit ${Number(item.fit_duration).toFixed(2)}s` : ""}</span></div>
          <div class="ocrebuild-voice-score-row"><For each={scoreParts}>{([label, value]) => <span>{label} {Number(value || 0).toFixed(2)}</span>}</For><Show when={parts.duration_penalty !== undefined}><span>时长惩罚 {Number(parts.duration_penalty || 0).toFixed(2)}</span></Show></div>
          <p>评分依据：以 16 秒 reference audio 为目标，对候选音频的音色、音高、亮度、能量、节奏、时长贴合度和表现力做声学特征比较。</p>
        </div>
      </Show>
    </div>;
  };

  return <section class="verify-dialog ocrebuild-compare-dialog ocrebuild-video-dialog ocrebuild-tts-dialog" data-workflow-id={wf().workflowId} classList={{ "is-dragged": Boolean(pos()) }} style={pos() ? { left: `${pos().left}px`, top: `${pos().top}px`, transform: "none" } : {}}>
    <div class="env-dialog-head ocrebuild-compare-head" onMouseDown={(event) => props.onDragStart(wf().workflowId, event)}>
      <div class="ocrebuild-compare-title"><span class="ocrebuild-compare-title-icon"><DocumentIcon /></span><h3>TTS Builder</h3><span class="ocrebuild-compare-title-meta">{wf().scope === "shot_plan" ? "Plan Default" : wf().shot?.shot_id}</span></div>
      <div class="ocrebuild-dialog-head-actions">
        <Show when={wf().scope !== "shot_plan"}><button class="ocrebuild-compare-play-button is-step-action" type="button" title="生成三个 TTS" aria-label="生成三个 TTS" disabled={!wf().shotProviderPrompts?.length || props.busy().startsWith("shot-tts-")} onClick={(event) => { event.stopPropagation(); void props.generateTTSForShot(wf().workflowId); }}><PlayIcon /></button></Show>
        <button class="secondary ocrebuild-compare-close-button" type="button" title="关闭" aria-label="关闭" onClick={() => props.onClose(wf().workflowId)}><CloseIcon /></button>
      </div>
    </div>
    <div class="ocrebuild-compare-scroll-body">
      <Show when={wf().error}><div class="banner bad openclip-banner">{wf().error}</div></Show>
      <Show when={wf().scope === "shot_plan"}>
        <section class="ocrebuild-voice-rec-panel">
          <div class="ocrebuild-tts-active-head">
            <div class="ocrebuild-tts-active-main">
              <div class="ocrebuild-tts-reference-row">
                <div class="ocrebuild-voice-gender-summary">{wf().voiceRecommendationResult ? props.voiceReferenceGenderText(wf()) : "待分析"}</div>
                <div class="ocrebuild-voice-reference-path">
                  <span>参考声音</span>
                  <code title={referencePath() || "未找到参考声音路径"}>{referenceName()}</code>
                  <button class="icon-action ocrebuild-reference-copy" type="button" title="复制完整路径" aria-label="复制完整路径" disabled={!referencePath()} onClick={() => void copyReferencePath()}><CopyIcon /></button>
                  <label class={`icon-action ocrebuild-reference-upload ${wf().referenceUploadPhase === "uploading" ? "is-uploading" : ""}`} title={wf().referenceUploadPhase === "uploading" ? "上传中" : "上传参考声音"} aria-label="上传参考声音">
                    <UploadIcon />
                    <input type="file" accept="audio/*,.wav,.mp3,.m4a,.aac,.flac" disabled={wf().referenceUploadPhase === "uploading"} onChange={(event) => { const input = event.currentTarget; void props.uploadShotTTSReferenceAudio(wf().workflowId, input.files).finally(() => { input.value = ""; }); }} />
                  </label>
                  <button class={`icon-action ocrebuild-reference-play ${referencePlaying() ? "is-playing" : ""}`} type="button" title={referencePlaying() ? "暂停参考声音" : "播放参考声音"} aria-label={referencePlaying() ? "暂停参考声音" : "播放参考声音"} disabled={!referenceAudioUrl()} onClick={() => props.toggleVoiceReferencePlayback(wf().workflowId)}>{referencePlaying() ? <PauseIcon /> : <PlayIcon />}</button>
                  <div class="ocrebuild-voice-builder-actions">
                    <button class="secondary" type="button" disabled={wf().phase === "voice_builder_g_running" || wf().phase === "voice_builder_q_running"} onClick={() => void props.runShotTTSVoiceBuilder(wf().workflowId, "g")}>{wf().phase === "voice_builder_g_running" ? "Builder-G 运行中..." : "Builder-G"}</button>
                    <button class="secondary" type="button" disabled={wf().phase === "voice_builder_g_running" || wf().phase === "voice_builder_q_running"} onClick={() => void props.runShotTTSVoiceBuilder(wf().workflowId, "q")}>{wf().phase === "voice_builder_q_running" ? "Builder-Q 运行中..." : "Builder-Q"}</button>
                  </div>
                </div>
              </div>
              <ReferenceWaveformSelector workflow={wf()} audioUrl={referenceAudioUrl()} onChange={props.updateVoiceReferenceClip} />
            </div>
          </div>
          <Show when={wf().voiceRecommendationResult}><div class="ocrebuild-voice-ref-summary"><span>Reference wav analysis</span></div></Show>
          <Show when={wf().voiceRecommendations?.length} fallback={<div class="ocrebuild-shot-empty"><strong>暂无推荐</strong><span>点击 Builder-G 或 Builder-Q，确认后运行对应工具生成推荐结果。</span></div>}>
            <div class="ocrebuild-voice-rec-list">
              <div class="ocrebuild-voice-rec-head"><span>序号</span><span>声音名称</span><span>提示词</span><span>播放</span><span>选用</span><span>测试</span><span>评分</span></div>
              <For each={wf().voiceRecommendations}>{(item, index) => renderVoiceRecommendationRow(item, index)}</For>
            </div>
          </Show>
        </section>
      </Show>
      <Show when={wf().scope !== "shot_plan"}>
        <section class="ocrebuild-tts-config-panel ocrebuild-shot-tts-lock-panel">
          <div class="ocrebuild-shot-tts-topbar">
            <div class="ocrebuild-shot-r2v-toolbar ocrebuild-shot-tts-toolbar">
              <label><span>目标总时长</span><input class="ocrebuild-shot-tts-duration-input" type="number" min="1" step="0.1" value={wf().targetDuration || ""} onInput={(event) => props.updateAssetTTSWorkflow(wf().workflowId, (prev) => prev ? { ...prev, targetDuration: Number(event.currentTarget.value) || "" } : prev)} /></label>
              <label><span>对白总字数</span><input readonly value={String(props.shotTTSFullText(wf().sceneItems).length)} /></label>
            </div>
          </div>
          <div class="ocrebuild-tts-model-grid">{props.renderShotTTSPromptCards(wf())}</div>
          <Show when={wf().phase === "shot_generating"}><div class="ocrebuild-compare-status-line"><strong>整段 TTS 生成中</strong><span>候选语音事件流正在写入 Debug Console。</span></div></Show>
          <Show when={wf().shotCandidates?.length || wf().shotFinal || wf().lockedTimeline || props.hasLockedTTSFile(wf())}><div class="ocrebuild-compare-grid ocrebuild-tts-candidate-grid">{props.renderShotTTSCandidates(wf())}{props.renderLockedTTSAudioPanel(wf())}</div></Show>
        </section>
        {props.renderLockedTimeline(wf())}
      </Show>
    </div>
  </section>;
}
