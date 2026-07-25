import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { api } from "../../../lib/api.ts";
import EditorClipPanel from "../editor/EditorClipPanel.jsx";
import EditorSearchPanel from "../editor/EditorSearchPanel.jsx";
import EditorTimeline from "../editor/EditorTimeline.jsx";
import {
  convertStaleFragmentToManual,
  createClipJobInput,
  editorReturnHash,
  isTransientClipJobPollError,
  newIdempotencyKey,
  normalizeClipItems,
  normalizeEditorPayload,
  normalizeManualSelection,
  normalizeSearchRun,
  selectionFromEditorNavigation,
  selectionFromFragment,
} from "../editor/editorModel.js";
import { formatTimelineMs } from "../editor/timelineModel.js";
import {
  mediaLibraryCapabilityView,
  normalizeMediaLibraryCapabilities,
} from "../mediaLibraryCapabilities.js";
import "../editor/mediaLibraryEditorViewport.css";

const ACTIVE_JOB_STATES = new Set(["queued", "running"]);
const FINAL_SEEK_TOLERANCE_MS = 50;
const FINAL_SEEK_TIMEOUT_MS = 2_000;
const SCRUB_PREVIEW_MIN_DELTA_MS = 8;

export default function MediaLibraryEditorPage(props) {
  let videoNode;
  let pollTimer = 0;
  let finalSeekTimer = 0;
  let loadGeneration = 0;
  let clipPollTransientFailures = 0;
  let lastScrubPreviewMs = null;
  const [editor, setEditor] = createSignal(null);
  const [busy, setBusy] = createSignal(true);
  const [loadError, setLoadError] = createSignal("");
  const [selection, setSelection] = createSignal({
    startMs: 0,
    endMs: 0,
    sourceScheme: "",
    sourceFragmentId: "",
    sourceRunId: "",
    sourceSearchId: "",
    sourceDialogueAssetKey: "",
    manualOverride: true,
  });
  const [playheadMs, setPlayheadMs] = createSignal(0);
  const [focusedFragmentRef, setFocusedFragmentRef] = createSignal("");
  const [visibleTracks, setVisibleTracks] = createSignal({ composite: true, dialogue: true, visual: true });
  const [staleNotice, setStaleNotice] = createSignal("");
  const [rangePreview, setRangePreview] = createSignal(null);
  const [isScrubbing, setIsScrubbing] = createSignal(false);
  const [pendingFinalSeekMs, setPendingFinalSeekMs] = createSignal(null);
  const [clipName, setClipName] = createSignal("");
  const [clipJob, setClipJob] = createSignal(null);
  const [clipError, setClipError] = createSignal("");
  const [clipActionBusy, setClipActionBusy] = createSignal(false);
  const [targetTaskId, setTargetTaskId] = createSignal(0);
  const [searchSources, setSearchSources] = createSignal(["media_library"]);
  const [searchUserText, setSearchUserText] = createSignal("");
  const [searchFragmentRefs, setSearchFragmentRefs] = createSignal([]);
  const [searchRun, setSearchRun] = createSignal(null);
  const [confirmedExternalLicenses, setConfirmedExternalLicenses] = createSignal(new Set());
  const [searchBusy, setSearchBusy] = createSignal(false);
  const [searchActionBusy, setSearchActionBusy] = createSignal(false);
  const [searchError, setSearchError] = createSignal("");
  const [sideMode, setSideMode] = createSignal("fragments");
  const [videoStatus, setVideoStatus] = createSignal("loading");
  const [capabilities, setCapabilities] = createSignal(
    normalizeMediaLibraryCapabilities(null),
  );
  const capabilityView = createMemo(
    () => mediaLibraryCapabilityView(capabilities()),
  );
  const usesProxyPreview = createMemo(() => String(editor()?.asset?.previewUrl || "").includes("/media_library/previews/"));
  const videoStatusLabel = createMemo(() => ({
    loading: "正在载入视频预览…",
    buffering: "网络波动，正在缓冲…",
    error: "视频预览加载失败，请刷新后重试。",
  }[videoStatus()] || ""));

  const allFragments = createMemo(() => {
    const value = editor();
    return value ? ["composite", "dialogue", "visual"].flatMap((scheme) => value.fragments[scheme]) : [];
  });
  const focusedFragment = createMemo(() => {
    const ref = focusedFragmentRef();
    return allFragments().find((fragment) => `${fragment.scheme}:${fragment.fragmentId}` === ref) || null;
  });
  const selectionValid = createMemo(() => {
    const value = selection();
    const duration = value.endMs - value.startMs;
    return Number.isInteger(value.startMs)
      && Number.isInteger(value.endMs)
      && value.startMs >= 0
      && value.endMs <= (editor()?.asset.durationMs || 0)
      && duration >= 250
      && duration <= 1_800_000;
  });

  function clearScrubState() {
    if (finalSeekTimer) window.clearTimeout(finalSeekTimer);
    finalSeekTimer = 0;
    lastScrubPreviewMs = null;
    setPendingFinalSeekMs(null);
    setIsScrubbing(false);
  }

  function cancelScrub() {
    if (videoNode) videoNode.pause();
    clearScrubState();
  }

  function scrubTarget(atMs) {
    const durationMs = editor()?.asset.durationMs || 0;
    return Math.max(0, Math.min(durationMs, Math.round(Number(atMs) || 0)));
  }

  function writeScrubPreview(atMs, exact = false) {
    if (!videoNode) return;
    const targetMs = scrubTarget(atMs);
    if (
      !exact
      && lastScrubPreviewMs !== null
      && Math.abs(targetMs - lastScrubPreviewMs) < SCRUB_PREVIEW_MIN_DELTA_MS
    ) return;
    lastScrubPreviewMs = targetMs;
    try {
      if (!exact && typeof videoNode.fastSeek === "function") {
        videoNode.fastSeek(targetMs / 1000);
      } else {
        videoNode.currentTime = targetMs / 1000;
      }
    } catch {
      // A video can briefly reject seeks while metadata/source is changing.
    }
  }

  function beginScrub(atMs) {
    const targetMs = scrubTarget(atMs);
    clearScrubState();
    if (videoNode) videoNode.pause();
    setRangePreview(null);
    setIsScrubbing(true);
    setPlayheadMs(targetMs);
    writeScrubPreview(targetMs);
  }

  function moveScrub(atMs) {
    if (!isScrubbing()) return;
    const targetMs = scrubTarget(atMs);
    setPlayheadMs(targetMs);
    writeScrubPreview(targetMs);
  }

  function endScrub(atMs) {
    const targetMs = scrubTarget(atMs);
    if (!isScrubbing()) beginScrub(targetMs);
    if (videoNode) videoNode.pause();
    setPlayheadMs(targetMs);
    setPendingFinalSeekMs(targetMs);
    writeScrubPreview(targetMs, true);
    if (!videoNode) {
      clearScrubState();
      return;
    }
    if (finalSeekTimer) window.clearTimeout(finalSeekTimer);
    finalSeekTimer = window.setTimeout(() => {
      setPlayheadMs(targetMs);
      clearScrubState();
    }, FINAL_SEEK_TIMEOUT_MS);
  }

  const loadEditor = async (showBusy = false) => {
    const generation = ++loadGeneration;
    if (showBusy) setBusy(true);
    setLoadError("");
    try {
      const payload = await api.mediaLibraryEditor(props.route.assetId, props.route.navigation);
      if (generation !== loadGeneration) return;
      const normalized = normalizeEditorPayload(payload, props.route.assetId);
      if (!normalized.valid) {
        setEditor(null);
        setLoadError(`GET editor 合同校验失败：${normalized.contractErrors.join("；")}`);
        return;
      }
      const firstLoad = !editor();
      setEditor(normalized);
      setVideoStatus("loading");
      if (firstLoad) {
        const initialSelection = selectionFromEditorNavigation(normalized);
        setSelection(initialSelection);
        setPlayheadMs(initialSelection.startMs);
        setClipName(`${normalized.asset.displayName} 片段`);
        if (normalized.navigation.targetValid && normalized.navigation.targetTaskId) {
          setTargetTaskId(normalized.navigation.targetTaskId);
        }
        if (normalized.navigation.matchedFragmentId) {
          const matched = allFragments().find((fragment) => fragment.fragmentId === normalized.navigation.matchedFragmentId);
          if (matched) setFocusedFragmentRef(`${matched.scheme}:${matched.fragmentId}`);
        }
      }
    } catch (error) {
      if (generation !== loadGeneration) return;
      setEditor(null);
      setLoadError(apiErrorMessage(error, "加载剪辑页失败"));
    } finally {
      if (generation === loadGeneration && showBusy) setBusy(false);
    }
  };

  createEffect(() => {
    const assetId = props.route.assetId;
    props.route.navigation;
    cancelScrub();
    setCapabilities(normalizeMediaLibraryCapabilities(null));
    void api.mediaLibraryCapabilities()
      .then((payload) => {
        if (props.route.assetId === assetId) {
          setCapabilities(
            normalizeMediaLibraryCapabilities(payload),
          );
        }
      })
      .catch(() => {
        if (props.route.assetId === assetId) {
          setCapabilities(normalizeMediaLibraryCapabilities(null));
        }
      });
    setEditor(null);
    setClipJob(null);
    clipPollTransientFailures = 0;
    setSearchRun(null);
    setConfirmedExternalLicenses(new Set());
    setSearchFragmentRefs([]);
    setSearchSources(["media_library"]);
    void loadEditor(true);
  });

  createEffect(() => {
    if (
      !capabilityView().searchEntryVisible
      && sideMode() === "search"
    ) {
      setSideMode("fragments");
    }
  });

  onCleanup(() => {
    loadGeneration += 1;
    if (pollTimer) window.clearTimeout(pollTimer);
    clearScrubState();
  });

  const seek = (atMs, autoplay = false) => {
    clearScrubState();
    const durationMs = editor()?.asset.durationMs || 0;
    const clamped = Math.max(0, Math.min(durationMs, Math.round(Number(atMs) || 0)));
    setPlayheadMs(clamped);
    if (videoNode) {
      videoNode.currentTime = clamped / 1000;
      if (autoplay) void videoNode.play().catch(() => {
        // A scrub can intentionally pause while the play promise is pending.
      });
    }
  };

  const onVideoTimeUpdate = () => {
    if (!videoNode || isScrubbing()) return;
    const currentMs = Math.max(0, Math.round(videoNode.currentTime * 1000));
    const preview = rangePreview();
    if (preview && currentMs >= preview.endMs) {
      videoNode.pause();
      videoNode.currentTime = preview.endMs / 1000;
      setPlayheadMs(preview.endMs);
      setRangePreview(null);
      return;
    }
    setPlayheadMs(currentMs);
  };

  const onVideoSeeked = () => {
    if (!videoNode) return;
    if (!isScrubbing()) {
      onVideoTimeUpdate();
      return;
    }
    const targetMs = pendingFinalSeekMs();
    if (targetMs === null) return;
    const currentMs = Math.max(0, Math.round(videoNode.currentTime * 1000));
    if (Math.abs(currentMs - targetMs) > FINAL_SEEK_TOLERANCE_MS) return;
    setPlayheadMs(targetMs);
    clearScrubState();
  };

  const focusFragment = (fragment) => {
    setFocusedFragmentRef(`${fragment.scheme}:${fragment.fragmentId}`);
    setStaleNotice("");
    seek(fragment.startMs);
    if (fragment.stale) {
      setStaleNotice("该片段来自 stale 分析，只能预览；不能初始化剪切选区或加入检索条件。");
      return;
    }
    const next = selectionFromFragment(fragment);
    const sourceSearchId = editor()?.navigation.searchId || "";
    if (next) setSelection({
      ...next,
      sourceSearchId,
      sourceDialogueAssetKey: sourceSearchId
        ? editor()?.navigation.dialogueAssetKey || ""
        : "",
    });
  };

  const previewFragment = (fragment) => {
    setFocusedFragmentRef(`${fragment.scheme}:${fragment.fragmentId}`);
    setRangePreview({ startMs: fragment.startMs, endMs: fragment.endMs });
    seek(fragment.startMs, true);
  };

  const previewSelection = () => {
    const value = selection();
    setRangePreview({ startMs: value.startMs, endMs: value.endMs });
    seek(value.startMs, true);
  };

  const updateSelectionManual = (startMs, endMs) => {
    const next = normalizeManualSelection(selection(), Math.round(startMs), Math.round(endMs), editor()?.asset.durationMs || 0);
    if (next) {
      setSelection(next);
      setStaleNotice("");
    }
  };

  const convertStale = () => {
    const next = convertStaleFragmentToManual(focusedFragment());
    if (!next) return;
    const sourceSearchId = editor()?.navigation.searchId || "";
    setSelection({
      ...next,
      sourceSearchId,
      sourceDialogueAssetKey: sourceSearchId
        ? editor()?.navigation.dialogueAssetKey || ""
        : "",
    });
    setStaleNotice("已显式转换为手动范围，原 fragment/run 身份已清除。");
  };

  const pollClipJob = async (clipJobId) => {
    if (!clipJobId) return;
    try {
      const result = await api.mediaLibraryClipJob(props.route.assetId, clipJobId);
      clipPollTransientFailures = 0;
      setClipJob(result);
      if (ACTIVE_JOB_STATES.has(result.status)) {
        pollTimer = window.setTimeout(() => void pollClipJob(clipJobId), 800);
      } else if (result.status === "completed") {
        await refreshClips();
      }
    } catch (error) {
      const message = apiErrorMessage(error, "读取剪切任务失败");
      if (
        !message.includes("clip_job_lost")
        && isTransientClipJobPollError(error)
        && clipPollTransientFailures < 60
      ) {
        clipPollTransientFailures += 1;
        setClipError("后端连接暂时中断，正在确认剪切任务状态…");
        pollTimer = window.setTimeout(
          () => void pollClipJob(clipJobId),
          500,
        );
        return;
      }
      clipPollTransientFailures = 0;
      setClipError(
        message.includes("clip_job_lost")
          ? "后端已重启，此进程内剪切任务已丢失（clip_job_lost）；已完成的派生片段仍保留。"
          : message,
      );
      setClipJob((current) => current ? { ...current, status: "failed", error: message } : current);
    }
  };

  const refreshClips = async () => {
    const current = editor();
    if (!current) return;
    try {
      const normalized = normalizeClipItems(
        await api.mediaLibraryClips(props.route.assetId),
        current.asset.durationMs,
      );
      if (!normalized.valid) throw new Error(`GET clips 合同失败：${normalized.errors.join("；")}`);
      setEditor((value) => value ? { ...value, clips: normalized.clips } : value);
    } catch (error) {
      setClipError(apiErrorMessage(error, "刷新派生片段失败"));
    }
  };

  const createClip = async () => {
    if (!capabilityView().editorMutationsEnabled) {
      setClipError("视频剪辑新任务功能当前已关闭。");
      return;
    }
    const value = editor();
    const input = createClipJobInput(value, selection(), clipName(), newIdempotencyKey("clip"));
    if (!input) {
      setClipError("剪切范围必须为 250ms–30min，名称不能为空，且素材合同必须有效。");
      return;
    }
    setClipActionBusy(true);
    setClipError("");
    clipPollTransientFailures = 0;
    try {
      const job = await api.mediaLibraryCreateClipJob(value.asset.assetId, input);
      setClipJob(job);
      if (ACTIVE_JOB_STATES.has(job.status)) void pollClipJob(job.clip_job_id);
      else if (job.status === "completed") await refreshClips();
    } catch (error) {
      setClipError(apiErrorMessage(error, "创建剪切任务失败"));
    } finally {
      setClipActionBusy(false);
    }
  };

  const cancelClipJob = async () => {
    if (!capabilityView().editorMutationsEnabled) return;
    const job = clipJob();
    if (!job || !ACTIVE_JOB_STATES.has(job.status)) return;
    setClipActionBusy(true);
    setClipError("");
    try {
      if (pollTimer) window.clearTimeout(pollTimer);
      const result = await api.mediaLibraryCancelClipJob(props.route.assetId, job.clip_job_id);
      setClipJob(result);
      if (ACTIVE_JOB_STATES.has(result.status)) {
        pollTimer = window.setTimeout(() => void pollClipJob(job.clip_job_id), 250);
      }
    } catch (error) {
      setClipError(apiErrorMessage(error, "取消剪切任务失败"));
    } finally {
      setClipActionBusy(false);
    }
  };

  const deleteClip = async (clip) => {
    if (!capabilityView().editorMutationsEnabled) return;
    if (!window.confirm(`删除派生片段“${clip.displayName}”？已被 StoryBoard 引用时服务端会拒绝。`)) return;
    setClipActionBusy(true);
    setClipError("");
    try {
      await api.mediaLibraryDeleteClip(props.route.assetId, clip.clipId);
      await refreshClips();
    } catch (error) {
      setClipError(apiErrorMessage(error, "删除派生片段失败"));
    } finally {
      setClipActionBusy(false);
    }
  };

  const importClip = async (clip) => {
    if (!capabilityView().editorMutationsEnabled) return;
    if (!targetTaskId()) {
      setClipError("请先选择目标 StoryBoard。");
      return;
    }
    setClipActionBusy(true);
    setClipError("");
    try {
      await api.mediaLibraryImportClip(props.route.assetId, clip.clipId, {
        target_task_id: targetTaskId(),
        requested_name: clip.displayName,
        search_id: editor()?.navigation.searchId || null,
        dialogue_asset_key: editor()?.navigation.dialogueAssetKey || null,
        idempotency_key: newIdempotencyKey("clip-import"),
      });
      setClipError("派生片段已导入目标 StoryBoard。");
    } catch (error) {
      setClipError(apiErrorMessage(error, "导入派生片段失败"));
    } finally {
      setClipActionBusy(false);
    }
  };

  const updateClip = async (clip, input) => {
    if (!capabilityView().clipSearchEnabled) return false;
    setClipActionBusy(true);
    setClipError("");
    try {
      await api.mediaLibraryUpdateClip(
        props.route.assetId,
        clip.clipId,
        input,
      );
      await refreshClips();
      setClipError(input.search_eligible === false
        ? "派生片段已移除全局素材检索；既有导入文件不受影响。"
        : "派生片段名称、标签和全局检索状态已保存。");
      return true;
    } catch (error) {
      setClipError(apiErrorMessage(error, "保存派生片段检索信息失败"));
      return false;
    } finally {
      setClipActionBusy(false);
    }
  };

  const toggleSearchRef = (fragment) => {
    if (!fragment || fragment.stale) {
      setSearchError("stale 片段不能加入搜索条件。");
      return;
    }
    const key = `${fragment.scheme}:${fragment.runId}:${fragment.fragmentId}`;
    setSearchFragmentRefs((current) => current.some((entry) => entry.key === key)
      ? current.filter((entry) => entry.key !== key)
      : [...current, {
        key,
        scheme: fragment.scheme,
        run_id: fragment.runId,
        fragment_id: fragment.fragmentId,
      }]);
  };

  const searchRefSelected = (fragment) => searchFragmentRefs().some((entry) => (
    entry.fragment_id === fragment.fragmentId && entry.scheme === fragment.scheme
  ));

  const runSearch = async () => {
    if (!capabilityView().searchEntryVisible) return;
    const sources = searchSources();
    if (!sources.length) return;
    if (sources.includes("external") && !targetTaskId()) {
      setSearchError("外部检索需要先选择有效 StoryBoard 目标。");
      return;
    }
    const input = {
      target_task_id: targetTaskId() || null,
      sources,
      fragment_refs: searchFragmentRefs().map(({ scheme, run_id, fragment_id }) => ({ scheme, run_id, fragment_id })),
      user_text: searchUserText().trim(),
      orientation: "any",
      limit: 12,
    };
    setSearchBusy(true);
    setSearchError("");
    setConfirmedExternalLicenses(new Set());
    try {
      await api.mediaLibraryEditorSearchPlan(props.route.assetId, input);
      const normalized = normalizeSearchRun(await api.mediaLibraryEditorSearchRun(props.route.assetId, input));
      if (!normalized.valid) throw new Error(`搜索响应合同失败：${normalized.errors.join("；")}`);
      setSearchRun(normalized);
    } catch (error) {
      setSearchError(apiErrorMessage(error, "对白/关键词检索失败"));
    } finally {
      setSearchBusy(false);
    }
  };

  const changeSearchSource = (source, checked) => {
    setSearchSources((current) => checked
      ? [...new Set([...current, source])]
      : current.filter((entry) => entry !== source));
  };

  const recordCandidateAction = async (candidate, actionKind) => {
    const run = searchRun();
    if (!run?.searchId || !candidate?.candidateId) return;
    try {
      await api.mediaLibraryEditorSearchAction(props.route.assetId, run.searchId, {
        action_kind: actionKind,
        source: candidate.source,
        candidate_id: candidate.candidateId,
        metadata: { entry_point: "editor" },
      });
    } catch (error) {
      console.warn("media_library_editor_search_action_failed", error);
    }
  };

  const openCandidateEditor = (candidate, selectedMatch = null) => {
    if (candidate.source !== "media_library" || !candidate.assetId || !candidate.allowedActions.includes("open_editor")) return;
    void recordCandidateAction(candidate, "open_editor");
    const query = new URLSearchParams();
    if (targetTaskId()) query.set("target_task_id", String(targetTaskId()));
    if (searchRun()?.searchId) query.set("search_id", searchRun().searchId);
    const match = selectedMatch || candidate.matchedFragments[0];
    if (match?.fragmentId) query.set("matched_fragment_id", match.fragmentId);
    if (Number.isInteger(match?.startMs) && Number.isInteger(match?.endMs)) {
      query.set("start_ms", String(match.startMs));
      query.set("end_ms", String(match.endMs));
    }
    query.set("return_to", "media_library_detail");
    window.location.hash = `#/media-library/${encodeURIComponent(candidate.assetId)}/editor?${query}`;
  };

  const importCandidate = async (candidate, action) => {
    if (!capabilityView().searchEntryVisible) return;
    if (!targetTaskId() || !searchRun()?.searchId) {
      setSearchError("候选导入需要真实 search_id 和目标 StoryBoard。");
      return;
    }
    setSearchActionBusy(true);
    setSearchError("");
    try {
      if (candidate.source === "external" && action === "import_whole") {
        if (!candidate.providerSearchId) throw new Error("外部候选缺少 provider_search_id，拒绝猜测导入来源。");
        const confirmLicense = confirmedExternalLicenses().has(candidate.candidateId);
        if (!confirmLicense) throw new Error("必须先显式确认该外部候选的 license，才能整条导入。");
        await api.mediaLibraryExternalSearchImport(targetTaskId(), {
          search_id: candidate.providerSearchId,
          candidate_ids: [candidate.candidateId],
          label_prefix: candidate.displayName,
          confirm_license: confirmLicense,
        });
      } else if (candidate.source === "media_library" && action === "import_original" && candidate.assetId) {
        await api.mediaLibraryEditorSearchImport(candidate.assetId, searchRun().searchId, {
          target_task_id: targetTaskId(),
          requested_name: candidate.displayName,
          search_id: searchRun().searchId,
          dialogue_asset_key: editor()?.navigation.dialogueAssetKey || null,
          idempotency_key: newIdempotencyKey("search-import"),
        });
      } else if (candidate.source === "media_library" && action === "import_clip" && candidate.candidateKind === "derived_clip" && candidate.sourceClipId) {
        await api.storyboardMediaLibraryImport(targetTaskId(), {
          source_kind: "media_library_clip",
          source_id: candidate.sourceClipId,
          target_task_id: targetTaskId(),
          requested_name: candidate.displayName,
          search_id: searchRun().searchId,
          dialogue_asset_key: editor()?.navigation.dialogueAssetKey || null,
          idempotency_key: newIdempotencyKey("search-clip-import"),
        });
      } else {
        throw new Error("候选来源与 allowed_actions 不匹配，已阻止越权导入。");
      }
      setSearchError(`“${candidate.displayName}”已导入目标 StoryBoard。`);
    } catch (error) {
      setSearchError(apiErrorMessage(error, "导入搜索候选失败"));
    } finally {
      setSearchActionBusy(false);
    }
  };

  return <div class="ml-editor-page">
    <Show when={busy()}><div class="ml-editor-loading">正在读取原视频、全部分析片段和派生片段…</div></Show>
    <Show when={loadError()}>
      <div class="ml-editor-fatal">
        <h2>无法打开视频剪辑页</h2>
        <p>{loadError()}</p>
        <div><button type="button" onClick={() => void loadEditor(true)}>重试</button><button type="button" onClick={() => { window.location.hash = "#/media-library"; }}>返回素材库</button></div>
      </div>
    </Show>
    <Show when={editor()}>{() => <div class="ml-editor-shell">
      <header class="ml-editor-header">
        <div>
          <button type="button" class="ml-editor-back" onClick={() => { window.location.hash = editorReturnHash(editor()); }}>← 返回</button>
          <h1>{editor().asset.displayName}</h1>
          <p>单原视频 · {formatTimelineMs(editor().asset.durationMs)} · {editor().capacity.fragmentCount} 个分析片段</p>
          <details class="ml-editor-technical-details">
            <summary>技术详情</summary>
            <dl>
              <div><dt>Source hash</dt><dd>{editor().sourceVersion}</dd></div>
              <Show when={editor().navigation.searchId}><div><dt>来源 Search ID</dt><dd>{editor().navigation.searchId}</dd></div></Show>
            </dl>
          </details>
        </div>
        <div class="ml-editor-header-context">
          <Show when={editor().navigation.returnTo === "storyboard_dialogue"}>
            <span classList={{ invalid: !editor().navigation.dialogueValid }}>
              {editor().navigation.dialogueValid ? "返回原 Dialogue" : "原 Dialogue 已失效，将返回 StoryBoard Task"}
            </span>
          </Show>
          <label>导入目标 StoryBoard
            <select value={targetTaskId() || ""} onChange={(event) => {
              const next = Number(event.currentTarget.value) || 0;
              setTargetTaskId(next);
              if (!next) setSearchSources((sources) => sources.filter((source) => source !== "external"));
            }}>
              <option value="">未选择</option>
              <For each={editor().importTargets}>{(target) =>
                <option value={target.taskId}>Task #{target.taskId} · {target.title}</option>
              }</For>
            </select>
          </label>
        </div>
      </header>
      <main class="ml-editor-workspace">
        <section class="ml-editor-stage">
          <div class="ml-editor-player-shell">
            <video
              ref={videoNode}
              src={editor().asset.previewUrl}
              controls
              preload="metadata"
              playsinline
              poster={editor().asset.thumbnailUrl || undefined}
              onLoadStart={() => setVideoStatus("loading")}
              onLoadedMetadata={() => setVideoStatus("ready")}
              onCanPlay={() => setVideoStatus("ready")}
              onWaiting={() => setVideoStatus("buffering")}
              onPlaying={() => setVideoStatus("playing")}
              onError={() => {
                setVideoStatus("error");
                cancelScrub();
              }}
              onTimeUpdate={onVideoTimeUpdate}
              onSeeked={onVideoSeeked}
              onPause={() => {
                setVideoStatus("ready");
                setRangePreview(null);
              }}
            />
            <Show when={usesProxyPreview()}><span class="ml-editor-preview-badge">流畅预览</span></Show>
            <Show when={videoStatusLabel()}><div class={`ml-editor-video-status is-${videoStatus()}`} role="status">{videoStatusLabel()}</div></Show>
          </div>
          <div class="ml-editor-range-controls">
            <div class="ml-editor-time-input">
              <span>入点</span>
              <strong aria-label="入点时间码">{formatTimelineMs(selection().startMs)}</strong>
              <label>可编辑毫秒<input
                aria-label="入点 ms"
                type="number"
                min="0"
                max={editor().asset.durationMs - 1}
                value={selection().startMs}
                onChange={(event) => updateSelectionManual(Number(event.currentTarget.value), selection().endMs)}
              /></label>
            </div>
            <div class="ml-editor-time-input">
              <span>出点</span>
              <strong aria-label="出点时间码">{formatTimelineMs(selection().endMs)}</strong>
              <label>可编辑毫秒<input
                aria-label="出点 ms"
                type="number"
                min="1"
                max={editor().asset.durationMs}
                value={selection().endMs}
                onChange={(event) => updateSelectionManual(selection().startMs, Number(event.currentTarget.value))}
              /></label>
            </div>
            <div class="ml-editor-range-summary">
              <span>选区时长</span>
              <strong classList={{ invalid: !selectionValid() }}>{formatTimelineMs(selection().endMs - selection().startMs)}</strong>
              <button type="button" disabled={!selectionValid()} onClick={previewSelection}>预览选区</button>
            </div>
            <Show when={capabilityView().editorMutationsEnabled}>
              <div class="ml-editor-clip-create">
                <label class="ml-editor-clip-name">片段名称<input value={clipName()} onInput={(event) => setClipName(event.currentTarget.value)} /></label>
                <button
                  type="button"
                  class="ml-editor-primary-button"
                  disabled={!selectionValid() || clipActionBusy() || ACTIVE_JOB_STATES.has(clipJob()?.status)}
                  onClick={() => void createClip()}
                >创建剪切任务</button>
              </div>
            </Show>
          </div>
          <Show when={staleNotice()}><div class="ml-editor-stale-notice">
            <span>{staleNotice()}</span>
            <Show when={focusedFragment()?.stale}><button type="button" onClick={convertStale}>转换为手动范围</button></Show>
          </div></Show>
        </section>
        <aside class="ml-editor-sidebar">
          <nav>
            <button type="button" classList={{ active: sideMode() === "fragments" }} onClick={() => setSideMode("fragments")}>片段</button>
            <Show when={capabilityView().searchEntryVisible}>
              <button type="button" classList={{ active: sideMode() === "search" }} onClick={() => setSideMode("search")}>素材检索</button>
            </Show>
            <button type="button" classList={{ active: sideMode() === "clips" }} onClick={() => {
              setSideMode("clips");
              void refreshClips();
            }}>派生片段</button>
          </nav>
          <Show when={sideMode() === "fragments"}>
            <section class="ml-editor-side-section">
              <header><div><h3>分析片段</h3><p>已过期结果使用斜纹显示，并保持只读。</p></div></header>
              <div class="ml-editor-fragment-index">
                <For each={allFragments()} fallback={<p class="ml-editor-empty">暂无分析片段，仍可手动剪切。</p>}>{(fragment) =>
                  <article classList={{
                    active: focusedFragmentRef() === `${fragment.scheme}:${fragment.fragmentId}`,
                    stale: fragment.stale,
                  }}>
                    <button type="button" onClick={() => focusFragment(fragment)}>
                      <span>{schemeLabel(fragment.scheme)} · {formatTimelineMs(fragment.startMs)}–{formatTimelineMs(fragment.endMs)}</span>
                      <strong>{fragment.label}</strong>
                    </button>
                    <button
                      type="button"
                      disabled={fragment.stale || !capabilityView().searchEntryVisible}
                      aria-pressed={searchRefSelected(fragment)}
                      title={fragment.stale ? "stale 片段不能加入检索" : searchRefSelected(fragment) ? "从检索条件中移除" : "加入检索条件"}
                      classList={{ selected: searchRefSelected(fragment) }}
                      onClick={() => toggleSearchRef(fragment)}
                    >{fragment.stale ? "stale" : searchRefSelected(fragment) ? "移出检索" : "加入检索"}</button>
                  </article>
                }</For>
              </div>
            </section>
          </Show>
          <Show when={sideMode() === "search" && capabilityView().searchEntryVisible}>
            <EditorSearchPanel
              importTargets={editor().importTargets}
              targetTaskId={targetTaskId()}
              sources={searchSources()}
              userText={searchUserText()}
              searchFragmentRefs={searchFragmentRefs()}
              busy={searchBusy()}
              actionBusy={searchActionBusy()}
              error={searchError()}
              searchRun={searchRun()}
              confirmedExternalLicenses={confirmedExternalLicenses()}
              onTargetTaskChange={(taskId) => {
                setTargetTaskId(taskId);
                if (!taskId) setSearchSources((sources) => sources.filter((source) => source !== "external"));
              }}
              onSourceChange={changeSearchSource}
              onUserTextChange={setSearchUserText}
              onClearRefs={() => setSearchFragmentRefs([])}
              onRun={() => void runSearch()}
              onPreview={(candidate) => void recordCandidateAction(candidate, "preview")}
              onOpenEditor={openCandidateEditor}
              onExternalLicenseConfirmation={(candidateId, confirmed) => setConfirmedExternalLicenses((current) => {
                const next = new Set(current);
                if (confirmed) next.add(candidateId);
                else next.delete(candidateId);
                return next;
              })}
              onImport={(candidate, action) => void importCandidate(candidate, action)}
            />
          </Show>
          <Show when={sideMode() === "clips"}>
            <EditorClipPanel
              clips={editor().clips}
              job={clipJob()}
              targetTaskId={targetTaskId()}
              actionBusy={clipActionBusy()}
              mutationsEnabled={capabilityView().editorMutationsEnabled}
              clipSearchEnabled={capabilityView().clipSearchEnabled}
              sourceArchived={editor().asset.archived}
              error={clipError()}
              onCancelJob={() => void cancelClipJob()}
              onDeleteClip={(clip) => void deleteClip(clip)}
              onImportClip={(clip) => void importClip(clip)}
              onUpdateClip={updateClip}
            />
          </Show>
        </aside>
      </main>
      <EditorTimeline
        durationMs={editor().asset.durationMs}
        fragments={editor().fragments}
        selection={selection()}
        playheadMs={playheadMs()}
        focusedFragmentRef={focusedFragmentRef()}
        visibleTracks={visibleTracks()}
        onTrackVisibility={(scheme, visible) => setVisibleTracks((current) => ({ ...current, [scheme]: visible }))}
        onScrubStart={beginScrub}
        onScrubMove={moveScrub}
        onScrubEnd={endScrub}
        onScrubCancel={cancelScrub}
        onSelectionManual={updateSelectionManual}
        onFragmentFocus={focusFragment}
        onFragmentPreview={previewFragment}
      />
    </div>}</Show>
  </div>;
}

function schemeLabel(scheme) {
  return { dialogue: "对白", visual: "画面", composite: "综合" }[scheme] || scheme;
}

function apiErrorMessage(error, fallback) {
  const message = error instanceof Error ? error.message : String(error || "");
  try {
    const parsed = JSON.parse(message);
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      return [detail.code, detail.user_message || detail.message, detail.suggested_action].filter(Boolean).join(" · ");
    }
  } catch {
    // The canonical request wrapper exposes non-JSON error bodies as Error.message.
  }
  return message || fallback;
}
