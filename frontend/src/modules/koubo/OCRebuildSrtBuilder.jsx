import { For, Index, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { ModelPresetCards } from "../../components/ModelPresetCards.jsx";

function splitPlainDialogue(value) {
  const text = String(value || "").trim();
  if (!text) return [];
  const parts = text.match(/[^。！？!?；;，,\n]+[。！？!?；;，,]?/g) || [text];
  return parts.map((item) => item.trim()).filter(Boolean);
}

function parseSrtCueRows(value) {
  const text = String(value || "").trim();
  if (!text) return [];
  const lines = text.split(/\r?\n/);
  const cues = [];
  let index = 0;
  while (index < lines.length) {
    while (index < lines.length && !lines[index].trim()) index += 1;
    if (index >= lines.length) break;
    const cueIndex = /^\d+$/.test(lines[index].trim()) ? lines[index++].trim() : String(cues.length + 1);
    const timeRange = lines[index]?.includes("-->") ? lines[index++].trim() : "";
    const textLines = [];
    while (index < lines.length && lines[index].trim()) textLines.push(lines[index++].trim());
    const cueText = textLines.join(" ").trim();
    if (timeRange || cueText) cues.push({ cue_index: cueIndex, time_range: timeRange, text: cueText || timeRange });
  }
  if (cues.length) return cues;
  return splitPlainDialogue(text).map((part, partIndex) => ({ cue_index: String(partIndex + 1), time_range: "", text: part }));
}

function srtRowId(shotId, sceneMarkId, cueIndex) {
  return [shotId, sceneMarkId, cueIndex || "1"].map((item) => String(item || "").replace(/\s+/g, "_")).join("__");
}

function srtTimeToSeconds(value) {
  const match = String(value || "").trim().match(/^(\d+):(\d+):(\d+)[,.](\d+)$/);
  if (!match) return null;
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) + Number(`0.${match[4]}`);
}

function srtCueDuration(row) {
  const [start, end] = String(row?.time_range || "").split("-->").map((item) => item.trim());
  const startSeconds = srtTimeToSeconds(start);
  const endSeconds = srtTimeToSeconds(end);
  if (startSeconds !== null && endSeconds !== null && endSeconds >= startSeconds) return endSeconds - startSeconds;
  const duration = Number(row?.duration || 0);
  return duration > 0 ? duration : null;
}

function positiveNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function ttsSoundEventInstructions(text) {
  const value = String(text || "");
  const matches = [...value.matchAll(/[【\[]([^】\]]+)[】\]]/g)];
  return matches.map((match) => String(match?.[1] || "").replace(/\s+/g, " ").trim()).filter(Boolean).map((event) => {
    const lower = event.toLowerCase();
    if (event.includes("咳") || lower.includes("cough")) {
      if (event.includes("三") || event.includes("3") || lower.includes("three")) return `遇到【${event}】时，不要朗读括号文字；请在该位置真实地轻微模拟咳嗽三声：“咳、咳、咳”，每声短促，三声之间有很短停顿，然后继续后面的正文。`;
      return `遇到【${event}】时，不要朗读括号文字；请在该位置自然模拟短促咳嗽声，然后继续后面的正文。`;
    }
    return `遇到【${event}】时，不要朗读括号文字；把它作为声音/表演动作提示自然执行，然后继续后面的正文。`;
  });
}

function applyTtsSoundEventInstructions(prompt, text) {
  const instructions = ttsSoundEventInstructions(text);
  const promptText = String(prompt || "").trim();
  if (!instructions.length) return promptText;
  const guidance = `声音动作规则：正文里的【】或[]内容是声音表演指令，不是要念出来的文字。\n${instructions.map((item) => `- ${item}`).join("\n")}`;
  const marker = promptText.match(/^正文\s*[:：]/m);
  if (marker && typeof marker.index === "number") return `${promptText.slice(0, marker.index).trimEnd()}\n${guidance}\n${promptText.slice(marker.index)}`.trim();
  return `${promptText}\n${guidance}`.trim();
}

export default function OCRebuildSrtBuilder(props) {
  const {
    ArrowsClockwiseIcon,
    CloseIcon,
    CodeIcon,
    CopyIcon,
    DocumentIcon,
    FilterIcon,
    PauseIcon,
    PlayIcon,
    SaveIcon,
  } = props.icons;

  const [srtRewritePrompt, setSrtRewritePrompt] = createSignal("");
  const [srtRewriteRows, setSrtRewriteRows] = createSignal([]);
  const [srtRewriteError, setSrtRewriteError] = createSignal("");
  const [srtRewriteShotFilter, setSrtRewriteShotFilter] = createSignal([]);
  const [srtRewriteSceneFilter, setSrtRewriteSceneFilter] = createSignal([]);
  const [srtRewriteFilterMenu, setSrtRewriteFilterMenu] = createSignal("");
  const [srtRewriteFilterQuery, setSrtRewriteFilterQuery] = createSignal("");
  const [srtRewriteNewFilterQuery, setSrtRewriteNewFilterQuery] = createSignal("");
  const [srtPromptPreviewOpen, setSrtPromptPreviewOpen] = createSignal(false);
  const [srtRunModelDialogOpen, setSrtRunModelDialogOpen] = createSignal(false);
  const [srtRewriteRowBusy, setSrtRewriteRowBusy] = createSignal({});
  const [srtTtsState, setSrtTtsState] = createSignal({});
  const [srtTtsPromptDialog, setSrtTtsPromptDialog] = createSignal(null);
  const [srtTtsPromptFormVersion, setSrtTtsPromptFormVersion] = createSignal(0);

  let srtPreviewAudio = null;
  let srtTtsProviderInputEl = null;
  let srtTtsModelInputEl = null;
  let srtTtsVoiceInputEl = null;
  let srtTtsTextInputEl = null;
  let srtTtsPromptInputEl = null;
  let srtTtsTempoInputEl = null;

  onCleanup(() => {
    if (srtPreviewAudio) srtPreviewAudio.pause();
  });

  function formatSrtCueDuration(row) {
    const duration = srtCueDuration(row);
    return duration !== null ? `${duration.toFixed(2)}s` : "-";
  }

  function defaultSrtRewritePrompt() {
    const product = props.draft()?.product_info || props.task()?.product_info || "当前目标产品/服务";
    return `请把原产品字幕改写成适合新产品/服务的口播字幕。\n\n目标产品/服务：${product}\n\n改写规则：\n1. 必须逐 Scene 一句对一句改写，不增删、不合并、不重排。\n2. 每句长度尽量接近原句，保持原来的口播节奏和生活化分享口吻。\n3. 只替换产品、品牌、卖点、使用方式等相关信息，不改人物、背景、镜头逻辑。\n4. 不要写成硬广说明书，不要加入医疗化、药品化、治疗、根治、保证有效等绝对化表达。\n5. 可以使用温和、真实的体验表达。\n6. 每行只输出该 Scene 的新字幕文案。`;
  }

  function srtOriginalForMark(mark) {
    return String(mark?.original_srt_text || mark?.source_srt_text || mark?.srt_text || "").trim();
  }

  function srtCueForScene(shotCues, mark, markIndex, sceneCount) {
    const markSrt = srtOriginalForMark(mark);
    const markCues = parseSrtCueRows(markSrt);
    if (markCues.length) return markCues;
    if (shotCues.length === sceneCount && shotCues[markIndex]) return [shotCues[markIndex]];
    if (shotCues[markIndex]) return [shotCues[markIndex]];
    return parseSrtCueRows(markSrt || "");
  }

  function srtRewriteRowsFromPlan() {
    return props.shotPlanShots().flatMap((shot) => {
      const shotSrt = String(shot?.reference?.original_srt_text || shot?.reference?.source_srt_text || shot?.reference?.srt_text || "").trim();
      const shotCues = parseSrtCueRows(shotSrt);
      const marks = props.editableSceneMarks(shot);
      if (!marks.length) {
        return shotCues.map((cue, cueIndex) => {
          const sceneMarkId = `${shot.shot_id}_scene_${String(cueIndex + 1).padStart(3, "0")}`;
          return {
            row_id: srtRowId(shot.shot_id, sceneMarkId, cue.cue_index),
            shot_id: shot.shot_id,
            scene_mark_id: sceneMarkId,
            scene_index: cueIndex + 1,
            cue_index: cue.cue_index,
            time_range: cue.time_range,
            virtual_scene: true,
            original_srt_text: cue.text,
            original_dialogue_text: cue.text,
            new_srt_text: cue.text,
            new_dialogue_text: cue.text,
          };
        });
      }
      return marks.flatMap((mark, markIndex) => {
        const cues = srtCueForScene(shotCues, mark, markIndex, marks.length);
        const fallbackOriginal = srtOriginalForMark(mark) || shotSrt;
        const currentText = String(mark.srt_text || fallbackOriginal).trim();
        const currentCues = parseSrtCueRows(currentText);
        const sourceCues = cues.length ? cues : [{ cue_index: String(markIndex + 1), time_range: "", text: props.plainSrtText(fallbackOriginal) || fallbackOriginal }];
        return sourceCues.map((cue, cueIndex) => {
          const currentCue = currentCues[cueIndex] || (currentCues.length === sourceCues.length ? currentCues[cueIndex] : null);
          const current = currentCue?.text || cue.text;
          const cueLabel = sourceCues.length > 1 ? `${cue.cue_index || cueIndex + 1}` : "";
          const rowSceneId = sourceCues.length > 1 ? `${mark.scene_mark_id}#${cueLabel}` : mark.scene_mark_id;
          return {
            row_id: srtRowId(shot.shot_id, rowSceneId, cue.cue_index || cueIndex + 1),
            shot_id: shot.shot_id,
            scene_mark_id: mark.scene_mark_id,
            scene_label: rowSceneId,
            scene_index: mark.scene_index,
            start: mark.start,
            end: mark.end,
            duration: mark.duration,
            cue_index: cue.cue_index || String(cueIndex + 1),
            time_range: cue.time_range,
            original_srt_text: cue.text,
            original_dialogue_text: cue.text,
            new_srt_text: current,
            new_dialogue_text: current,
          };
        });
      });
    }).filter((row) => row.shot_id && row.scene_mark_id);
  }

  createEffect(() => {
    if (!props.open()) return;
    setSrtRewritePrompt((value) => value || defaultSrtRewritePrompt());
    setSrtRewriteRows(srtRewriteRowsFromPlan());
    setSrtRewriteError("");
  });

  const srtRewriteFilteredRows = createMemo(() => {
    const originalKeyword = srtRewriteFilterQuery().trim().toLowerCase();
    const newKeyword = srtRewriteNewFilterQuery().trim().toLowerCase();
    const shotSet = new Set(srtRewriteShotFilter());
    const sceneSet = new Set(srtRewriteSceneFilter());
    const filtered = srtRewriteRows().map((row, index) => ({ ...row, __index: index })).filter((row) => {
      const originalText = `${row.original_dialogue_text || ""} ${props.plainSrtText(row.original_srt_text || "")}`.toLowerCase();
      const newText = `${row.new_dialogue_text || ""} ${props.plainSrtText(row.new_srt_text || "")}`.toLowerCase();
      const sceneKey = `${row.shot_id || ""}::${row.scene_mark_id || ""}`;
      if (shotSet.size && !shotSet.has(row.shot_id)) return false;
      if (sceneSet.size && !sceneSet.has(sceneKey)) return false;
      if (originalKeyword && !originalText.includes(originalKeyword)) return false;
      if (newKeyword && !newText.includes(newKeyword)) return false;
      return true;
    });
    const spanFor = (start, keyFn) => {
      const key = keyFn(filtered[start]);
      let end = start + 1;
      while (end < filtered.length && keyFn(filtered[end]) === key) end += 1;
      return end - start;
    };
    return filtered.map((row, index) => {
      const sceneKey = (item) => `${item?.shot_id || ""}::${item?.scene_mark_id || ""}`;
      const showShot = index === 0 || filtered[index - 1]?.shot_id !== row.shot_id;
      const showScene = index === 0 || sceneKey(filtered[index - 1]) !== sceneKey(row);
      return { ...row, __showShot: showShot, __shotRowspan: showShot ? spanFor(index, (item) => item?.shot_id || "") : 0, __showScene: showScene, __sceneRowspan: showScene ? spanFor(index, sceneKey) : 0 };
    });
  });

  const srtRewriteShotOptions = createMemo(() => Array.from(new Set(srtRewriteRows().map((row) => row.shot_id).filter(Boolean))).map((shotId) => ({ id: shotId, label: shotId })));
  const srtRewriteSceneOptions = createMemo(() => {
    const seen = new Set();
    const options = [];
    for (const row of srtRewriteRows()) {
      const key = `${row.shot_id || ""}::${row.scene_mark_id || ""}`;
      if (!row.shot_id || !row.scene_mark_id || seen.has(key)) continue;
      seen.add(key);
      options.push({ id: key, label: row.scene_label || row.scene_mark_id, shot_id: row.shot_id });
    }
    const shotSet = new Set(srtRewriteShotFilter());
    return shotSet.size ? options.filter((item) => shotSet.has(item.shot_id)) : options;
  });

  function toggleSrtMultiFilter(kind, value) {
    const setter = kind === "shot" ? setSrtRewriteShotFilter : setSrtRewriteSceneFilter;
    setter((items) => items.includes(value) ? items.filter((item) => item !== value) : [...items, value]);
  }

  function clearSrtMultiFilter(kind) {
    if (kind === "shot") {
      setSrtRewriteShotFilter([]);
      setSrtRewriteSceneFilter([]);
    } else {
      setSrtRewriteSceneFilter([]);
    }
  }

  function srtFilterActive(kind) {
    if (kind === "shot") return Boolean(srtRewriteShotFilter().length);
    if (kind === "scene") return Boolean(srtRewriteSceneFilter().length);
    if (kind === "original") return Boolean(srtRewriteFilterQuery().trim());
    if (kind === "new") return Boolean(srtRewriteNewFilterQuery().trim());
    return false;
  }

  function reloadSrtRewriteRows() {
    setSrtRewriteRows(srtRewriteRowsFromPlan());
    setSrtRewriteError("");
  }

  function srtRowKey(row) {
    return row?.row_id || srtRowId(row?.shot_id, row?.scene_mark_id, row?.cue_index);
  }

  function updateSrtRewriteRowByKey(key, value) {
    setSrtRewriteRows((rows) => rows.map((row) => srtRowKey(row) === key ? { ...row, new_srt_text: value, new_dialogue_text: props.plainSrtText(value) || value } : row));
  }

  function copyOriginalSrtRow(row) {
    const value = row?.original_dialogue_text || props.plainSrtText(row?.original_srt_text || "") || "";
    const key = srtRowKey(row);
    setSrtRewriteRows((rows) => rows.map((item) => srtRowKey(item) === key ? { ...item, new_srt_text: value, new_dialogue_text: value } : item));
  }

  function srtRowRewriteRunning(row) {
    return Boolean(srtRewriteRowBusy()[srtRowKey(row)]);
  }

  function applySrtRowsToShotPlan(rows) {
    const byKey = new Map();
    const virtualByShot = new Map();
    for (const row of rows || []) {
      if (row.virtual_scene) {
        const items = virtualByShot.get(row.shot_id) || [];
        items.push(row);
        virtualByShot.set(row.shot_id, items);
        continue;
      }
      const key = `${row.shot_id}::${row.scene_mark_id}`;
      const items = byKey.get(key) || [];
      items.push(row);
      byKey.set(key, items);
    }
    props.setShotPlan((prev) => ({
      ...prev,
      shots: (prev?.shots || []).map((shot) => {
        const reference = shot.reference || {};
        const sceneMarks = (reference.scene_marks || []).map((mark) => {
          const markRows = byKey.get(`${shot.shot_id}::${mark.scene_mark_id}`) || [];
          if (!markRows.length) return mark;
          const original = markRows.map((row) => row.original_srt_text || row.original_dialogue_text || "").filter(Boolean).join("\n") || srtOriginalForMark(mark);
          const finalText = markRows.map((row) => row.new_srt_text || "").filter(Boolean).join("\n");
          return { ...mark, original_srt_text: original, srt_text: finalText };
        });
        const virtualRows = virtualByShot.get(shot.shot_id) || [];
        const shotSrt = virtualRows.length ? virtualRows.map((row) => row.new_srt_text || "").filter(Boolean).join("\n") : reference.srt_text;
        return { ...shot, reference: { ...reference, scene_marks: sceneMarks, srt_text: shotSrt } };
      }),
    }));
    props.setAssetPromptPackages((prev) => {
      const next = { ...prev };
      for (const row of rows || []) {
        const sidecar = next[row.shot_id];
        if (!sidecar) continue;
        const updatePkg = (item) => String(item?.scene_mark_id || "") === row.scene_mark_id ? { ...item, srt_text: row.new_srt_text || "" } : item;
        next[row.shot_id] = Array.isArray(sidecar?.scenes) ? { ...sidecar, scenes: sidecar.scenes.map(updatePkg) } : updatePkg(sidecar);
      }
      return next;
    });
  }

  async function generateSrtRewriteRows() {
    if (!props.task()?.id) return false;
    setSrtRewriteError("");
    props.setBusy("srt-rewrite-generate");
    props.setError("");
    try {
      const res = await props.api.generateSrtRewrite(props.task().id, {
        prompt: srtRewritePrompt() || defaultSrtRewritePrompt(),
        rows: srtRewriteRows(),
        run_model_provider: props.draft()?.run_model_provider || props.task()?.run_model_provider || "",
        run_model_id: props.draft()?.run_model_id || props.task()?.run_model_id || "",
      });
      setSrtRewriteRows((res.rows || []).map((row) => ({ ...row, original_dialogue_text: row.original_dialogue_text || props.plainSrtText(row.original_srt_text || ""), new_dialogue_text: props.plainSrtText(row.new_srt_text || "") || row.new_srt_text || "" })));
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSrtRewriteError(message);
      props.setError(message);
      props.emitDebugError?.(err, { family: "srt_rewrite", request_id: "srt-rewrite-generate" });
      return false;
    } finally {
      props.setBusy("");
    }
  }

  async function runSrtRewriteWithSelectedModel() {
    const ok = await generateSrtRewriteRows();
    if (ok) setSrtRunModelDialogOpen(false);
  }

  function selectRunModelPreset(selection) {
    props.updateDraft("run_model_provider", selection.providerID);
    props.updateDraft("run_model_id", selection.modelID);
  }

  async function generateSrtRewriteSingleRow(row) {
    if (!props.task()?.id || !row) return;
    const key = srtRowKey(row);
    setSrtRewriteError("");
    setSrtRewriteRowBusy((prev) => ({ ...prev, [key]: true }));
    try {
      const res = await props.api.generateSrtRewrite(props.task().id, {
        prompt: srtRewritePrompt() || defaultSrtRewritePrompt(),
        rows: [row],
        run_model_provider: props.draft()?.run_model_provider || props.task()?.run_model_provider || "",
        run_model_id: props.draft()?.run_model_id || props.task()?.run_model_id || "",
      });
      const nextRow = (res.rows || [])[0];
      if (nextRow) {
        setSrtRewriteRows((rows) => rows.map((item) => srtRowKey(item) === key ? { ...item, ...nextRow, original_dialogue_text: nextRow.original_dialogue_text || item.original_dialogue_text || props.plainSrtText(nextRow.original_srt_text || ""), new_dialogue_text: props.plainSrtText(nextRow.new_srt_text || "") || nextRow.new_srt_text || "" } : item));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSrtRewriteError(message);
      props.emitDebugError?.(err, { family: "srt_rewrite_row", request_id: key });
    } finally {
      setSrtRewriteRowBusy((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  }

  async function saveSrtRewriteRows() {
    if (!props.task()?.id) return;
    setSrtRewriteError("");
    props.setBusy("srt-rewrite-save");
    props.setError("");
    try {
      const res = await props.api.saveSrtRewrite(props.task().id, { rows: srtRewriteRows() });
      applySrtRowsToShotPlan(res.rows || srtRewriteRows());
      setSrtRewriteRows((res.rows || srtRewriteRows()).map((row) => ({ ...row, original_dialogue_text: row.original_dialogue_text || row.original_srt_text || "", new_dialogue_text: props.plainSrtText(row.new_srt_text || "") || row.new_srt_text || "" })));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSrtRewriteError(message);
      props.setError(message);
      props.emitDebugError?.(err, { family: "srt_rewrite", request_id: "srt-rewrite-save" });
    } finally {
      props.setBusy("");
    }
  }

  function srtRowText(row) {
    return String(row?.new_srt_text || row?.new_dialogue_text || row?.original_dialogue_text || "").trim();
  }

  function srtRowTtsWorkflowId(row) {
    return props.normalizeWorkflowId(`srt_builder_${row?.row_id || `${row?.shot_id}_${row?.scene_mark_id}_${row?.cue_index || ""}`}`);
  }

  function srtRowShot(row) {
    return props.shotPlanShots().find((item) => String(item?.shot_id || "") === String(row?.shot_id || "")) || null;
  }

  function updateSrtTtsState(row, patch) {
    const key = srtRowKey(row);
    setSrtTtsState((prev) => ({ ...prev, [key]: { ...(prev[key] || {}), ...patch } }));
  }

  function srtTtsForRow(row) {
    const key = srtRowKey(row);
    return srtTtsState()[key] || {};
  }

  function playSrtAudio(src) {
    if (!src) return;
    if (srtPreviewAudio) srtPreviewAudio.pause();
    srtPreviewAudio = new Audio(src);
    void srtPreviewAudio.play();
  }

  function srtEffectiveTtsPrompt(card, text) {
    const prompt = String(card?.currentPrompt || "").trim();
    const textValue = String(text || "").trim();
    let rendered = prompt;
    if (card?.provider === "google") {
      if (!prompt || prompt === "{text}") rendered = textValue;
      else if (prompt.includes("{text}")) rendered = prompt.replaceAll("{text}", textValue);
      else rendered = `${prompt}\n\n正文：${textValue}`;
      return applyTtsSoundEventInstructions(rendered, textValue);
    }
    return applyTtsSoundEventInstructions(rendered, textValue);
  }

  function srtTtsConfigKey(card, text) {
    const prompt = srtEffectiveTtsPrompt(card, text);
    return JSON.stringify({ provider: card?.provider || "", model: card?.model || "", voiceId: card?.voiceId || "", prompt, userInstruction: card?.userInstruction || "", text: String(text || "").trim(), tempo: props.ttsCardTempo(card) });
  }

  async function srtDefaultTtsPrompt(row) {
    const text = srtRowText(row);
    const shot = srtRowShot(row);
    const scene = { scene_mark_id: row?.row_id || row?.scene_mark_id || "", shot_id: row?.shot_id || "", srt_text: text, planned_duration: srtCueDuration(row) };
    const planSelection = shot?.tts_selection || props.shotPlan()?.plan_a_tts_selection || null;
    try {
      await props.ensureTTSModelConfig();
      const cards = props.applyTTSPlanSelection(props.defaultTTSPrompts(scene), planSelection, shot, [scene], null);
      return cards.find((item) => item.recommendation) || cards.find((item) => item.enabled && item.hasApiKey) || cards[0] || null;
    } catch (err) {
      if (!planSelection?.provider || !planSelection?.model || !(planSelection?.voice_id || planSelection?.voice)) throw err;
      const prompt = planSelection.prompt || props.promptForRecommendedVoice(planSelection, shot, null);
      return { provider: planSelection.provider, model: planSelection.model, voiceId: planSelection.voice_id || planSelection.voice, currentPrompt: prompt, tempo: props.tempoFromSelection(planSelection), recommendation: planSelection, userInstruction: "默认使用 Shot Plan 推荐声音。" };
    }
  }

  async function generateSrtRowTTS(row, promptCard = null) {
    if (!props.task()?.id || !row) return;
    const text = String(promptCard?.textOverride || srtRowText(row)).trim();
    if (!text) {
      updateSrtTtsState(row, { phase: "error", error: "这一行没有可生成的 SRT 文案" });
      return;
    }
    const card = promptCard || await srtDefaultTtsPrompt(row);
    if (!card?.provider || !card?.model || !card?.voiceId) {
      updateSrtTtsState(row, { phase: "error", error: "没有可用的默认 TTS 模型或 voice" });
      return;
    }
    const configKey = srtTtsConfigKey(card, text);
    updateSrtTtsState(row, { phase: "generating", error: "" });
    try {
      await props.api.streamCompareAssetTTS(props.task().id, {
        workflow_id: srtRowTtsWorkflowId(row),
        shot_id: row.shot_id,
        scene_mark_id: row.row_id || row.scene_mark_id,
        srt_text: text,
        prompts: [{ provider: card.provider, model: card.model, voice_id: card.voiceId, prompt: srtEffectiveTtsPrompt(card, text) || props.defaultTTSPrompt({ srt_text: text }, card.provider), text, user_instruction: card.userInstruction || "", tempo: props.ttsCardTempo(card), target_duration: null, fit_to_duration: false }],
      }, (event) => {
        props.emitStreamDebug?.(event, { family: "srt_row_tts", workflow_id: srtRowTtsWorkflowId(row), row_id: row.row_id });
        if (event.type === "completed") {
          const audioVersion = Date.now();
          const audioSrc = `${props.rebuildAssetUrl(event.output)}?v=${audioVersion}`;
          updateSrtTtsState(row, { phase: "ready", audioSrc, audioVersion, output: event.output, rawOutput: event.raw_output || "", provider: event.provider, model: event.model, voiceId: event.voice_id || card.voiceId, durationSeconds: event.duration_seconds, targetDuration: event.target_duration, rawDuration: event.raw_duration, fitDuration: event.fit_duration, speedFactor: event.speed_factor, tempo: event.tempo || props.ttsCardTempo(card), stretched: event.stretched, fitWarnings: event.fit_warnings || [], configKey, error: "" });
          playSrtAudio(audioSrc);
        }
        if (event.type === "failed") updateSrtTtsState(row, { phase: "error", error: event.detail || "TTS 生成失败" });
      });
    } catch (err) {
      updateSrtTtsState(row, { phase: "error", error: err instanceof Error ? err.message : String(err) });
    }
  }

  async function playOrGenerateSrtRowTTS(row) {
    const state = srtTtsForRow(row);
    const card = await srtDefaultTtsPrompt(row);
    const configKey = srtTtsConfigKey(card, String(card?.textOverride || srtRowText(row)).trim());
    if (state.audioSrc && state.configKey === configKey) {
      playSrtAudio(state.audioSrc);
      return;
    }
    await generateSrtRowTTS(row, card);
  }

  function srtGroupRows(row, scope) {
    if (!row) return [];
    return srtRewriteRows().filter((item) => scope === "shot" ? item.shot_id === row.shot_id : item.shot_id === row.shot_id && item.scene_mark_id === row.scene_mark_id);
  }

  function srtGroupTtsRow(row, scope) {
    const rows = srtGroupRows(row, scope);
    const text = rows.map((item) => srtRowText(item)).filter(Boolean).join("\n");
    const original = rows.map((item) => item.original_dialogue_text || props.plainSrtText(item.original_srt_text || "")).filter(Boolean).join("\n");
    const duration = rows.reduce((sum, item) => sum + (srtCueDuration(item) || 0), 0);
    return { row_id: `group_${scope}_${scope === "shot" ? row?.shot_id : `${row?.shot_id}_${row?.scene_mark_id}`}`, shot_id: row?.shot_id || "", scene_mark_id: scope === "shot" ? `${row?.shot_id || "shot"}_all_scenes` : row?.scene_mark_id || "", scene_label: scope === "shot" ? "Shot" : row?.scene_label || row?.scene_mark_id || "", duration, original_dialogue_text: original, original_srt_text: original, new_srt_text: text, new_dialogue_text: text };
  }

  async function playOrGenerateSrtGroupTTS(row, scope) {
    await playOrGenerateSrtRowTTS(srtGroupTtsRow(row, scope));
  }

  async function openSrtTtsPromptDialog(row) {
    try {
      const card = await srtDefaultTtsPrompt(row);
      setSrtTtsPromptFormVersion((value) => value + 1);
      setSrtTtsPromptDialog({ row, provider: card?.provider || "", model: card?.model || "", voiceId: card?.voiceId || "", prompt: card?.currentPrompt || props.defaultTTSPrompt({ srt_text: srtRowText(row) }, card?.provider || ""), text: srtRowText(row), tempo: props.ttsCardTempo(card), userInstruction: card?.userInstruction || "逐句生成，严格按照该行字幕朗读。" });
    } catch (err) {
      setSrtTtsPromptFormVersion((value) => value + 1);
      setSrtTtsPromptDialog({ row, provider: "", model: "", voiceId: "", prompt: props.defaultTTSPrompt({ srt_text: srtRowText(row) }, ""), text: srtRowText(row), tempo: null, userInstruction: "逐句生成，严格按照该行字幕朗读。", error: err instanceof Error ? err.message : String(err) });
    }
  }

  function srtTtsPromptFormValue(dialog) {
    srtTtsPromptFormVersion();
    return { provider: srtTtsProviderInputEl?.value ?? dialog?.provider ?? "", model: srtTtsModelInputEl?.value ?? dialog?.model ?? "", voiceId: srtTtsVoiceInputEl?.value ?? dialog?.voiceId ?? "", text: srtTtsTextInputEl?.value ?? dialog?.text ?? "", prompt: srtTtsPromptInputEl?.value ?? dialog?.prompt ?? "", tempo: positiveNumber(srtTtsTempoInputEl?.value ?? dialog?.tempo ?? 0) };
  }

  function updateSrtTtsPromptPreview() {
    setSrtTtsPromptFormVersion((value) => value + 1);
  }

  async function confirmSrtPromptTTS() {
    const dialog = srtTtsPromptDialog();
    if (!dialog?.row) return;
    const values = srtTtsPromptFormValue(dialog);
    await generateSrtRowTTS(dialog.row, { provider: values.provider, model: values.model, voiceId: values.voiceId, currentPrompt: values.prompt, userInstruction: dialog.userInstruction, textOverride: values.text, tempo: values.tempo });
  }

  const renderHeaderFilter = (kind) => {
    const isShot = kind === "shot";
    const options = isShot ? srtRewriteShotOptions() : srtRewriteSceneOptions();
    return <div class="ocrebuild-srt-th-filter">
      <button class={`ocrebuild-srt-th-filter-button ${srtFilterActive(kind) ? "is-active" : ""}`} type="button" title="筛选" aria-label="筛选" onClick={(event) => { event.stopPropagation(); setSrtRewriteFilterMenu((value) => value === kind ? "" : kind); }}><FilterIcon /></button>
      <Show when={srtRewriteFilterMenu() === kind}><div class="ocrebuild-srt-filter-menu">
        <button class="ocrebuild-srt-filter-clear" type="button" onClick={() => clearSrtMultiFilter(kind)}>{isShot ? "全部 Shot" : "全部 Scene"}</button>
        <For each={options}>{(item) => <label><input type="checkbox" checked={(isShot ? srtRewriteShotFilter() : srtRewriteSceneFilter()).includes(item.id)} onChange={() => toggleSrtMultiFilter(kind, item.id)} />{item.label}</label>}</For>
      </div></Show>
    </div>;
  };

  const renderDialogueFilter = (kind) => {
    const isNew = kind === "new";
    return <div class="ocrebuild-srt-th-filter">
      <button class={`ocrebuild-srt-th-filter-button ${srtFilterActive(kind) ? "is-active" : ""}`} type="button" title="筛选" aria-label="筛选" onClick={(event) => { event.stopPropagation(); setSrtRewriteFilterMenu((value) => value === kind ? "" : kind); }}><FilterIcon /></button>
      <Show when={srtRewriteFilterMenu() === kind}><div class="ocrebuild-srt-filter-menu ocrebuild-srt-text-filter-menu">
        <input class="ocrebuild-srt-filter-search" value={isNew ? srtRewriteNewFilterQuery() : srtRewriteFilterQuery()} onInput={(event) => isNew ? setSrtRewriteNewFilterQuery(event.currentTarget.value) : setSrtRewriteFilterQuery(event.currentTarget.value)} placeholder={isNew ? "筛选新对话" : "筛选原对话"} />
        <button class="ocrebuild-srt-filter-clear" type="button" onClick={() => isNew ? setSrtRewriteNewFilterQuery("") : setSrtRewriteFilterQuery("")}>清除筛选</button>
      </div></Show>
    </div>;
  };

  const bindRowTextarea = (el, row) => {
    const key = srtRowKey(row);
    if (el.dataset.rowKey !== key) {
      el.dataset.rowKey = key;
      el.value = row?.new_srt_text || "";
    }
  };

  const renderRunModelDialog = () => <Show when={srtRunModelDialogOpen() && props.draft()}>
    <div class="drawer-backdrop openclip-model-overlay" onClick={() => setSrtRunModelDialogOpen(false)} />
    <section class="verify-dialog openflow-model-dialog openclip-prompt-model-dialog openclip-run-model-dialog">
      <div class="env-dialog-head openclip-model-dialog-head"><div class="openclip-model-header-text"><h3>SRT Builder Run Model</h3></div></div>
      <div class="openflow-prompt-model-grid openclip-model-dialog-body model-preset-dialog-body">
        <ModelPresetCards
          items={props.runModels?.() || []}
          provider={props.draft().run_model_provider}
          model={props.draft().run_model_id}
          onSelect={selectRunModelPreset}
          aria-label="SRT builder run model preset"
        />
      </div>
      <div class="openflow-model-dialog-summary openclip-model-selection"><div class="openflow-model-dialog-summary-body openclip-selection-card"><div class="openclip-run-selection-content"><em>{props.modelDetail(props.selectedRunModel())}</em><span>SRT Builder: {srtRewriteRows().length} rows</span></div></div></div>
      <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions openclip-run-model-actions"><button class="secondary openclip-model-cancel" onClick={() => setSrtRunModelDialogOpen(false)}>Cancel</button><button class="openclip-model-confirm" disabled={!props.draft().run_model_provider || !props.draft().run_model_id || props.busy() === "srt-rewrite-generate"} onClick={() => void runSrtRewriteWithSelectedModel()}>{props.busy() === "srt-rewrite-generate" ? "Running..." : "Run SRT Builder"}</button></div>
    </section>
  </Show>;

  const renderPromptPreview = () => <Show when={srtPromptPreviewOpen()}>
    <div class="drawer-backdrop" onClick={() => setSrtPromptPreviewOpen(false)} />
    <section class="verify-dialog openclip-prompt-preview-dialog">
      <div class="env-dialog-head"><div><h3>SRT Prompt</h3><p>{(srtRewritePrompt() || "").length.toLocaleString()} characters</p></div><button class="secondary" onClick={() => setSrtPromptPreviewOpen(false)}>Close</button></div>
      <textarea class="skill-editor openclip-prompt-preview-textarea" value={srtRewritePrompt()} onInput={(event) => setSrtRewritePrompt(event.currentTarget.value)} />
    </section>
  </Show>;

  const renderSrtTtsPromptDialog = () => {
    const dialog = srtTtsPromptDialog();
    if (!dialog) return null;
    const state = () => srtTtsForRow(dialog.row);
    const formValue = () => srtTtsPromptFormValue(dialog);
    const effectivePrompt = () => {
      const values = formValue();
      return srtEffectiveTtsPrompt({ provider: values.provider, currentPrompt: values.prompt }, values.text);
    };
    return <Show when={dialog}>
      <div class="drawer-backdrop openclip-model-overlay" onClick={() => setSrtTtsPromptDialog(null)} />
      <section class="verify-dialog openclip-prompt-preview-dialog ocrebuild-srt-tts-prompt-dialog">
        <div class="env-dialog-head">
          <div><h3>逐句 TTS Prompt</h3><p>{dialog.row?.shot_id} · {dialog.row?.scene_label || dialog.row?.scene_mark_id} · {formatSrtCueDuration(dialog.row)}</p></div>
          <button class="secondary" onClick={() => setSrtTtsPromptDialog(null)}>Close</button>
        </div>
        <div class="ocrebuild-srt-tts-dialog-body">
          <div class="ocrebuild-srt-tts-dialog-grid">
            <label class="openflow-field"><span>Provider</span><input ref={(el) => { srtTtsProviderInputEl = el; el.value = dialog.provider || ""; }} onInput={updateSrtTtsPromptPreview} /></label>
            <label class="openflow-field"><span>Model</span><input ref={(el) => { srtTtsModelInputEl = el; el.value = dialog.model || ""; }} onInput={updateSrtTtsPromptPreview} /></label>
            <label class="openflow-field"><span>Voice</span><input ref={(el) => { srtTtsVoiceInputEl = el; el.value = dialog.voiceId || ""; }} onInput={updateSrtTtsPromptPreview} /></label>
            <label class="openflow-field"><span>Tempo</span><input type="number" min="0.1" step="0.0001" ref={(el) => { srtTtsTempoInputEl = el; el.value = dialog.tempo || ""; }} onInput={updateSrtTtsPromptPreview} /></label>
          </div>
          <label class="openflow-field"><span>Text</span><textarea class="skill-editor ocrebuild-srt-tts-text" ref={(el) => { srtTtsTextInputEl = el; el.value = dialog.text || ""; }} onInput={updateSrtTtsPromptPreview} /></label>
          <label class="openflow-field"><span>TTS Prompt</span><textarea class="skill-editor openclip-prompt-preview-textarea" ref={(el) => { srtTtsPromptInputEl = el; el.value = dialog.prompt || ""; }} onInput={updateSrtTtsPromptPreview} /></label>
          <label class="openflow-field"><span>Final Prompt Sent</span><textarea class="skill-editor openclip-prompt-preview-textarea" readOnly value={effectivePrompt()} /></label>
          <Show when={state().audioSrc}>
            <div class="tts-preview-row">
              <audio controls preload="metadata" src={state().audioSrc} />
              <span>{state().durationSeconds ? `${Number(state().durationSeconds).toFixed(2)}s` : "生成完成，可直接试听"}<Show when={state().tempo || state().speedFactor}> · tempo {Number(state().tempo || state().speedFactor).toFixed(4)}x</Show><Show when={state().rawDuration}> · raw {Number(state().rawDuration).toFixed(2)}s</Show></span>
            </div>
          </Show>
          <Show when={state().error || dialog.error}><p class="tts-preview-error">{state().error || dialog.error}</p></Show>
          <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions">
            <button class="secondary" type="button" onClick={() => setSrtTtsPromptDialog(null)}>Cancel</button>
            <button class="openclip-model-confirm" type="button" disabled={!formValue().provider || !formValue().model || !formValue().voiceId || !formValue().text || state().phase === "generating"} onClick={() => void confirmSrtPromptTTS()}>{state().phase === "generating" ? "Generating..." : "Generate & Preview"}</button>
          </div>
        </div>
      </section>
    </Show>;
  };

  return <>
    <Show when={props.open()}>
      <div class="drawer-backdrop" onClick={() => props.onClose()} />
      <section class="skill-drawer openflow-config-drawer openflow-prompt-drawer ocrebuild-srt-builder-drawer">
        <div class="skill-drawer-head ocrebuild-srt-builder-head">
          <div class="ocrebuild-srt-titlebar">
            <div class="ocrebuild-drawer-title-row ocrebuild-srt-title-row"><h3>SRT Builder</h3><div class="ocrebuild-srt-builder-meta">{props.shotCount()} Shots · {srtRewriteRows().length} Lines</div></div>
          </div>
          <div class="openflow-dialog-head-actions">
            <button class="icon-action openflow-icon-action success" type="button" title="重载 Shot / Scene 行" aria-label="重载 Shot / Scene 行" disabled={!props.task() || props.busy() === "srt-rewrite-generate" || props.busy() === "srt-rewrite-save"} onClick={() => reloadSrtRewriteRows()}><ArrowsClockwiseIcon /></button>
            <button class="icon-action openflow-icon-action primary" type="button" title="Run Model 更新 SRT" aria-label="Run Model 更新 SRT" disabled={props.busy() === "srt-rewrite-generate" || !srtRewriteRows().length || !props.draft()?.run_model_provider || !props.draft()?.run_model_id} onClick={() => setSrtRunModelDialogOpen(true)}>{props.busy() === "srt-rewrite-generate" ? <PauseIcon /> : <CodeIcon />}</button>
            <button class="icon-action openflow-icon-action" type="button" title="查看 SRT Prompt" aria-label="查看 SRT Prompt" onClick={() => setSrtPromptPreviewOpen(true)}><DocumentIcon /></button>
            <button class="icon-action openflow-icon-action success" type="button" title="保存新 SRT 到 Scene" aria-label="保存新 SRT 到 Scene" disabled={props.busy() === "srt-rewrite-save" || !srtRewriteRows().length} onClick={() => void saveSrtRewriteRows()}><SaveIcon /></button>
            <button class="icon-action openflow-icon-action close" type="button" title="Close" aria-label="Close" onClick={() => props.onClose()}><CloseIcon /></button>
          </div>
        </div>
        <div class="openflow-config-drawer-body ocrebuild-srt-body">
          <Show when={srtRewriteError()}><div class="banner bad openclip-banner">{srtRewriteError()}</div></Show>
          <section class="ocrebuild-srt-table-card">
            <div class="ocrebuild-srt-table-wrap">
              <table class="ocrebuild-srt-table">
                <thead><tr><th><div class="ocrebuild-srt-th-content"><span>Shot</span>{renderHeaderFilter("shot")}</div></th><th><div class="ocrebuild-srt-th-content"><span>Scene</span>{renderHeaderFilter("scene")}</div></th><th><div class="ocrebuild-srt-th-content"><span>原对话</span>{renderDialogueFilter("original")}</div></th><th><div class="ocrebuild-srt-th-content"><span>新对话</span>{renderDialogueFilter("new")}</div></th><th>Action</th></tr></thead>
                <tbody>
                  <Index each={srtRewriteFilteredRows()}>{(row) => {
                    const item = () => row();
                    const shotGroup = () => srtGroupTtsRow(item(), "shot");
                    const sceneGroup = () => srtGroupTtsRow(item(), "scene");
                    return <tr>
                      <th class={`ocrebuild-srt-merged-cell ${item().__showShot ? "" : "is-merged-blank"}`}><Show when={item().__showShot}><div class="ocrebuild-srt-merged-content is-shot-layout"><span class="ocrebuild-srt-id-pill">{item().shot_id}</span><button class="icon-action ocrebuild-srt-cell-play" type="button" title="播放整个 Shot TTS" aria-label="播放整个 Shot TTS" disabled={srtTtsForRow(shotGroup()).phase === "generating"} onClick={() => void playOrGenerateSrtGroupTTS(item(), "shot")}>{srtTtsForRow(shotGroup()).phase === "generating" ? <PauseIcon /> : <PlayIcon />}</button></div></Show></th>
                      <td class={`ocrebuild-srt-merged-cell ${item().__showScene ? "" : "is-merged-blank"}`}><Show when={item().__showScene}><div class="ocrebuild-srt-merged-content is-scene-layout"><span class="ocrebuild-srt-id-pill is-scene">{item().scene_label || item().scene_mark_id}</span><span class="ocrebuild-srt-scene-controls"><span class="ocrebuild-srt-time-pill">{formatSrtCueDuration(item())}</span><button class="icon-action ocrebuild-srt-cell-play" type="button" title="播放整个 Scene TTS" aria-label="播放整个 Scene TTS" disabled={srtTtsForRow(sceneGroup()).phase === "generating"} onClick={() => void playOrGenerateSrtGroupTTS(item(), "scene")}>{srtTtsForRow(sceneGroup()).phase === "generating" ? <PauseIcon /> : <PlayIcon />}</button></span></div></Show></td>
                      <td><p class="ocrebuild-srt-original-text"><em>{item().original_dialogue_text || props.plainSrtText(item().original_srt_text || "") || "-"}</em><span class="ocrebuild-srt-original-duration"><span>{formatSrtCueDuration(item())}</span><button class="icon-action ocrebuild-srt-copy-action" type="button" title="复制原对话到新对话" aria-label="复制原对话到新对话" onClick={() => copyOriginalSrtRow(item())}><CopyIcon /></button></span></p></td>
                      <td><textarea ref={(el) => bindRowTextarea(el, item())} data-row-id={srtRowKey(item())} onInput={(event) => updateSrtRewriteRowByKey(srtRowKey(item()), event.currentTarget.value)} /></td>
                      <td><div class="ocrebuild-srt-row-actions">
                        <button class="icon-action openflow-icon-action primary" type="button" title="通过原对话替换产品生成新对话" aria-label="通过原对话替换产品生成新对话" disabled={srtRowRewriteRunning(item())} onClick={() => void generateSrtRewriteSingleRow(item())}>{srtRowRewriteRunning(item()) ? <PauseIcon /> : <CodeIcon />}</button>
                        <button class="icon-action openflow-icon-action success" type="button" title={srtTtsForRow(item()).audioSrc ? "播放默认 TTS" : "用推荐默认 TTS 生成并播放"} aria-label="用推荐默认 TTS 生成并播放" disabled={srtTtsForRow(item()).phase === "generating"} onClick={() => void playOrGenerateSrtRowTTS(item())}>{srtTtsForRow(item()).phase === "generating" ? <PauseIcon /> : <PlayIcon />}</button>
                        <button class="icon-action openflow-icon-action" type="button" title="查看提示词并逐句生成 TTS" aria-label="查看提示词并逐句生成 TTS" disabled={srtTtsForRow(item()).phase === "generating"} onClick={() => void openSrtTtsPromptDialog(item())}><DocumentIcon /></button>
                        <Show when={srtTtsForRow(item()).error}><span class="ocrebuild-srt-tts-error">{srtTtsForRow(item()).error}</span></Show>
                      </div></td>
                    </tr>;
                  }}</Index>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </section>
    </Show>
    {renderRunModelDialog()}
    {renderSrtTtsPromptDialog()}
    {renderPromptPreview()}
  </>;
}
