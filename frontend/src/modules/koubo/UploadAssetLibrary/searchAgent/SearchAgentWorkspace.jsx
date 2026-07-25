import { For, Index, Show, createMemo, createSignal, onMount } from "solid-js";
import { emitDebugError } from "../../../../debug/debugAdapter.js";
import {
  ASSET_SEARCH_SOURCES,
  MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT,
  MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS,
  assetSearchSourceLabel,
  buildMediaLibraryEditorHash,
  candidateSupportsAction,
  candidateSupportsImport,
  formatSearchRange,
  mediaLibraryFragmentKindLabel,
} from "../../mediaLibrarySearchModel.js";
import FloatingAssetMenu from "../components/FloatingAssetMenu.jsx";
import FlowIcon from "../components/FlowIcon.jsx";
import "./searchAgent.css";

const MAX_IMPORT_SELECTION = 12;

const PROVIDERS = ASSET_SEARCH_SOURCES;

const MEDIA_TYPES = [
  { key: "image", label: "图片" },
  { key: "video", label: "视频" },
  { key: "audio", label: "音频" },
];

const ASPECTS = [
  { key: "auto", label: "Auto" },
  { key: "16:9", label: "16:9" },
  { key: "9:16", label: "9:16" },
  { key: "1:1", label: "1:1" },
];

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function candidateKey(candidate) {
  const id = String(candidate?.candidate_id || "");
  if (!id) return "";
  return `${String(candidate?.candidate_kind || "external")}:${id}`;
}

function candidateImportId(candidate) {
  return String(candidate?.candidate_id || "");
}

function licenseTone(candidate) {
  const license = candidate?.license || {};
  if (license.license_status === "unconfirmed") return "warning";
  if (license.requires_attribution) return "info";
  return "ok";
}

function candidateMeta(candidate) {
  const bits = [
    assetSearchSourceLabel(candidate?.provider || candidate?.source),
    candidate?.orientation,
    candidate?.width && candidate?.height ? `${candidate.width}x${candidate.height}` : "",
    candidate?.duration_seconds
      ? `${Number(candidate.duration_seconds).toFixed(1)}s`
      : candidate?.duration_ms
        ? `${(Number(candidate.duration_ms) / 1000).toFixed(1)}s`
        : "",
    candidate?.score !== undefined ? `score ${Math.round(Number(candidate.score || 0) * 100)}` : "",
  ].filter(Boolean);
  return bits.join(" · ");
}

function candidateTitle(candidate) {
  return String(candidate?.display_name || candidate?.title || candidateKey(candidate) || "候选素材");
}

function candidateMediaType(candidate) {
  const explicit = String(candidate?.media_type || "");
  if (explicit) return explicit;
  return candidate?.global_media_library || candidate?.source === "media_library" || candidate?.provider === "media_library" ? "video" : "image";
}

function candidateDisplayAspect(candidate, requestedAspect = "auto") {
  if (requestedAspect === "9:16") return "portrait";
  if (requestedAspect === "16:9") return "landscape";
  if (candidate?.orientation === "portrait") return "portrait";
  if (candidate?.orientation === "landscape") return "landscape";
  const width = Number(candidate?.width || 0);
  const height = Number(candidate?.height || 0);
  if (width && height) return height > width ? "portrait" : "landscape";
  return "landscape";
}

function detailText(error) {
  if (!error) return "";
  if (error instanceof Error) return error.message;
  return String(error);
}

export function createSearchAgentController(options) {
  const taskId = () => options.task?.()?.id;
  const [settings, setSettings] = createSignal(null);
  const [providerStatus, setProviderStatus] = createSignal({});
  const [searchText, setSearchText] = createSignal("");
  const [sources, setSources] = createSignal(new Set(["local", "media_library", "pexels", "pixabay", "wikimedia"]));
  const [mediaTypes, setMediaTypes] = createSignal(new Set(["image", "video"]));
  const [aspect, setAspect] = createSignal("auto");
  const [limitPerSource, setLimitPerSource] = createSignal(12);
  const [plan, setPlan] = createSignal(null);
  const [searchId, setSearchId] = createSignal("");
  const [candidates, setCandidates] = createSignal([]);
  const [selectedIds, setSelectedIds] = createSignal(new Set());
  const [activeCandidateId, setActiveCandidateId] = createSignal("");
  const [providerEvents, setProviderEvents] = createSignal({});
  const [eventLog, setEventLog] = createSignal([]);
  const [phase, setPhase] = createSignal("idle");
  const [statusText, setStatusText] = createSignal("");
  const [error, setError] = createSignal("");
  const [importBusy, setImportBusy] = createSignal(false);
  const [confirmImportOpen, setConfirmImportOpen] = createSignal(false);
  const [importedAssets, setImportedAssets] = createSignal([]);
  const [failedImports, setFailedImports] = createSignal([]);
  const [apiKeyDraft, setApiKeyDraft] = createSignal({ pexels: "", pixabay: "", unsplash: "" });
  const [settingsBusy, setSettingsBusy] = createSignal(false);
  const [sourceExport, setSourceExport] = createSignal(null);
  const [plannerDegraded, setPlannerDegraded] = createSignal(false);

  const requestPayload = () => {
    const selectedSources = Array.from(sources());
    const selectedMediaTypes = Array.from(mediaTypes());
    const currentPlan = plan();
    return {
      text: searchText(),
      media_types: selectedMediaTypes,
      aspect: aspect(),
      sources: selectedSources,
      limit_per_source: Number(limitPerSource()) || 12,
      plan: currentPlan?.edited ? { ...currentPlan, sources: selectedSources, media_types: selectedMediaTypes, aspect: aspect() } : null,
    };
  };

  const selectedCandidates = createMemo(() => {
    const ids = selectedIds();
    return candidates().filter((item) => ids.has(candidateKey(item)));
  });

  const activeCandidate = createMemo(() => {
    const id = activeCandidateId();
    return candidates().find((item) => candidateKey(item) === id) || candidates()[0] || null;
  });

  const addLog = (event) => {
    setEventLog((previous) => [{ ...event, at: Date.now() }, ...previous].slice(0, 80));
  };

  const updateSearchText = (value) => {
    setSearchText(value);
    setPlan(null);
    setPlannerDegraded(false);
  };

  const ensureSettings = async () => {
    if (!taskId()) return null;
    const result = await options.api.assetLibrarySearchSettings(taskId());
    setSettings(result?.settings || {});
    setProviderStatus(result?.provider_status || {});
    const defaults = result?.settings?.defaults || {};
    const enabledSources = PROVIDERS
      .filter((item) => result?.settings?.sources?.[item.key]?.enabled !== false)
      .map((item) => item.key);
    if (enabledSources.length) setSources(new Set(enabledSources));
    if (Array.isArray(defaults.media_types) && defaults.media_types.length) {
      setMediaTypes(new Set(defaults.media_types.filter((item) => MEDIA_TYPES.some((type) => type.key === item))));
    }
    if (defaults.aspect) setAspect(defaults.aspect);
    return result;
  };

  const toggleSource = (key) => {
    setSources((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next.size ? next : previous;
    });
  };

  const toggleMediaType = (key) => {
    setMediaTypes((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next.size ? next : previous;
    });
  };

  const createPlan = async () => {
    if (!taskId()) throw new Error("Task is not loaded");
    setError("");
    setPhase("planning");
    setStatusText("生成检索计划");
    try {
      const result = await options.api.assetLibrarySearchPlan(taskId(), requestPayload());
      setPlan(result?.plan || null);
      setPlannerDegraded(Boolean(result?.planner_degraded || result?.plan?.planner_degraded || result?.plan?.degraded));
      if (result?.plan?.sources?.length) setSources(new Set(result.plan.sources));
      if (result?.plan?.media_types?.length) setMediaTypes(new Set(result.plan.media_types));
      if (result?.plan?.aspect) setAspect(result.plan.aspect);
      setStatusText(result?.plan?.degraded ? "计划已生成，检索质量可能下降" : "计划已生成");
      setPhase("idle");
      return result?.plan || null;
    } catch (err) {
      setError(detailText(err));
      setStatusText("计划生成失败");
      setPhase("idle");
      throw err;
    }
  };

  const createStoryboardPlan = async () => {
    if (!taskId()) throw new Error("Task is not loaded");
    setError("");
    setPhase("planning");
    setStatusText("按 StoryBoard 生成批量计划");
    try {
      const result = await options.api.assetLibrarySearchStoryboardPlan(taskId(), requestPayload());
      setPlan(result?.plan || null);
      setPlannerDegraded(Boolean(result?.planner_degraded || result?.plan?.planner_degraded || result?.plan?.degraded));
      if (result?.plan?.sources?.length) setSources(new Set(result.plan.sources));
      if (result?.plan?.media_types?.length) setMediaTypes(new Set(result.plan.media_types));
      if (result?.plan?.aspect) setAspect(result.plan.aspect);
      setStatusText(`批量计划已生成：${asArray(result?.plan?.queries).length} 个查询`);
      setPhase("idle");
      return result?.plan || null;
    } catch (err) {
      setError(detailText(err));
      setStatusText("批量计划生成失败");
      setPhase("idle");
      throw err;
    }
  };

  const updatePlanQuery = (index, patch) => {
    setPlan((current) => {
      if (!current) return current;
      const queries = asArray(current.queries).map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item);
      return { ...current, queries, edited: true };
    });
  };

  const addPlanQuery = () => {
    setPlan((current) => {
      const base = current || {
        summary: searchText(),
        media_types: Array.from(mediaTypes()),
        aspect: aspect(),
        sources: Array.from(sources()),
        queries: [],
      };
      const type = Array.from(mediaTypes())[0] || "image";
      return {
        ...base,
        queries: [...asArray(base.queries), { query: searchText() || "realistic stock media", language: "en", media_type: type, priority: asArray(base.queries).length + 1 }],
        edited: true,
      };
    });
  };

  const removePlanQuery = (index) => {
    setPlan((current) => {
      if (!current) return current;
      const queries = asArray(current.queries).filter((_, itemIndex) => itemIndex !== index).map((item, itemIndex) => ({ ...item, priority: itemIndex + 1 }));
      return { ...current, queries, edited: true };
    });
  };

  const updateApiKeyDraft = (provider, value) => {
    setApiKeyDraft((previous) => ({ ...previous, [provider]: value }));
  };

  const saveProviderKeys = async () => {
    if (!taskId()) throw new Error("Task is not loaded");
    const providerKeys = Object.fromEntries(Object.entries(apiKeyDraft()).filter(([, value]) => String(value || "").trim()));
    if (!Object.keys(providerKeys).length) return null;
    setSettingsBusy(true);
    setError("");
    try {
      const result = await options.api.saveAssetLibrarySearchSettings(taskId(), {
        settings: settings() || {},
        provider_keys: providerKeys,
      });
      setSettings(result?.settings || settings());
      setProviderStatus(result?.provider_status || {});
      setApiKeyDraft({ pexels: "", pixabay: "", unsplash: "" });
      setStatusText("素材源凭据已保存");
      return result;
    } catch (err) {
      setError(detailText(err));
      setStatusText("素材源凭据保存失败");
      throw err;
    } finally {
      setSettingsBusy(false);
    }
  };

  const handleSearchEvent = (event) => {
    addLog(event);
    if (event.type === "started") {
      setSearchId(event.search_id || "");
      setCandidates([]);
      setSelectedIds(new Set());
      setImportedAssets([]);
      setFailedImports([]);
      setProviderEvents({});
      setPlannerDegraded(false);
      setStatusText("开始检索");
    } else if (event.type === "plan") {
      setPlan(event.plan || null);
      setPlannerDegraded(Boolean(event.planner_degraded || event.plan?.planner_degraded || event.plan?.degraded));
      setStatusText("检索计划已确认");
    } else if (event.type === "provider.started") {
      setProviderEvents((previous) => ({ ...previous, [event.provider]: { status: "searching" } }));
      setStatusText(`正在检索 ${event.provider}`);
    } else if (event.type === "candidate.batch") {
      setCandidates((previous) => {
        const seen = new Set(previous.map(candidateKey));
        const next = [...previous];
        for (const item of asArray(event.items)) {
          const key = candidateKey(item);
          if (!key || seen.has(key)) continue;
          seen.add(key);
          next.push(item);
        }
        return next;
      });
    } else if (event.type === "provider.completed") {
      setProviderEvents((previous) => ({ ...previous, [event.provider]: { status: event.status || "ok", returned: event.returned || 0, kept: event.kept || 0 } }));
    } else if (event.type === "completed") {
      setPlannerDegraded(Boolean(event.planner_degraded || event.plan?.planner_degraded || event.plan?.degraded || plannerDegraded()));
      const finalItems = asArray(event.items || event.candidates);
      if (finalItems.length) {
        const finalKeys = new Set(finalItems.map(candidateKey).filter(Boolean));
        setCandidates(finalItems);
        setSelectedIds((previous) => new Set([...previous].filter((key) => finalKeys.has(key))));
        setActiveCandidateId((current) => finalKeys.has(current) ? current : candidateKey(finalItems[0]));
      }
      setPhase("idle");
      setStatusText(`检索完成：${event.candidate_count || finalItems.length || candidates().length} 个候选`);
    } else if (event.type === "failed") {
      setPhase("idle");
      setError(event.detail || "检索失败");
      setStatusText("检索失败");
    }
  };

  const startSearch = async () => {
    if (!taskId()) throw new Error("Task is not loaded");
    setError("");
    setPhase("searching");
    setStatusText("准备检索");
    try {
      await options.api.streamAssetLibrarySearch(taskId(), requestPayload(), handleSearchEvent);
    } catch (err) {
      setPhase("idle");
      setError(detailText(err));
      setStatusText("检索失败");
      throw err;
    }
  };

  const toggleCandidate = (candidate) => {
    if (!candidateSupportsImport(candidate)) return;
    const key = candidateKey(candidate);
    if (!key) return;
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else if (next.size < MAX_IMPORT_SELECTION) next.add(key);
      else {
        setStatusText(`一次最多导入 ${MAX_IMPORT_SELECTION} 个候选`);
        return previous;
      }
      return next;
    });
    setActiveCandidateId(key);
  };

  const removeCandidate = (candidate) => {
    const key = candidateKey(candidate);
    if (!key) return;
    setCandidates((previous) => previous.filter((item) => candidateKey(item) !== key));
    setSelectedIds((previous) => {
      const next = new Set(previous);
      next.delete(key);
      return next;
    });
    setActiveCandidateId((previous) => previous === key ? "" : previous);
  };

  const importSelected = async () => {
    if (!taskId()) throw new Error("Task is not loaded");
    const ids = selectedCandidates().map(candidateImportId).filter(Boolean);
    if (!searchId() || !ids.length) return null;
    setImportBusy(true);
    setError("");
    try {
      const result = await options.api.importAssetLibrarySearch(taskId(), {
        search_id: searchId(),
        candidate_ids: ids,
        label_prefix: plan()?.summary || searchText(),
        confirm_license: true,
      });
      setImportedAssets(asArray(result?.imported));
      setFailedImports(asArray(result?.failed));
      setConfirmImportOpen(false);
      setSelectedIds(new Set());
      options.onAssetLibraryResult?.(result);
      setStatusText(`导入完成：${asArray(result?.imported).length} 成功，${asArray(result?.failed).length} 失败`);
      return result;
    } catch (err) {
      setError(detailText(err));
      setStatusText("导入失败");
      throw err;
    } finally {
      setImportBusy(false);
    }
  };

  const exportSourceList = async () => {
    if (!taskId()) throw new Error("Task is not loaded");
    setError("");
    try {
      const result = await options.api.exportAssetLibrarySearchSourceList(taskId());
      setSourceExport(result || null);
      setStatusText(`来源清单已导出：${result?.item_count || 0} 条`);
      return result;
    } catch (err) {
      setError(detailText(err));
      setStatusText("来源清单导出失败");
      throw err;
    }
  };

  return {
    settings,
    providerStatus,
    searchText,
    setSearchText: updateSearchText,
    sources,
    mediaTypes,
    aspect,
    setAspect,
    limitPerSource,
    setLimitPerSource,
    plan,
    searchId,
    candidates,
    selectedIds,
    selectedCandidates,
    activeCandidate,
    setActiveCandidateId,
    providerEvents,
    eventLog,
    phase,
    statusText,
    error,
    importBusy,
    confirmImportOpen,
    setConfirmImportOpen,
    importedAssets,
    failedImports,
    sourceExport,
    plannerDegraded,
    ensureSettings,
    toggleSource,
    toggleMediaType,
    createPlan,
    createStoryboardPlan,
    updatePlanQuery,
    addPlanQuery,
    removePlanQuery,
    startSearch,
    toggleCandidate,
    removeCandidate,
    importSelected,
    exportSourceList,
    apiKeyDraft,
    updateApiKeyDraft,
    saveProviderKeys,
    settingsBusy,
  };
}

function ProviderBadge(props) {
  const status = () => props.status?.[props.item.key] || {};
  const configured = () => props.providerStatus?.[props.item.key]?.configured;
  return <button class={`ual-search-chip ${props.active ? "is-active" : ""}`} type="button" onClick={props.onClick}>
    <span>{props.item.label}</span>
    <small>{status().status || (configured() || props.item.keyless ? "ready" : "no key")}</small>
  </button>;
}

export function SearchBriefCard(props) {
  const queries = () => asArray(props.controller.plan()?.queries);
  return <section class={`ual-search-brief ${props.compact ? "is-compact" : ""}`}>
    <Show when={!props.compact}>
      <div class="ual-search-section-title">
        <strong>Search Brief</strong>
        <Show when={props.controller.plannerDegraded()}>
          <span class="ual-search-badge is-warning">规划降级 · 已继续检索</span>
        </Show>
      </div>
      <p>{props.controller.plan()?.summary || props.controller.searchText() || "输入素材需求后生成检索计划"}</p>
    </Show>
    <div class="ual-search-query-editor">
      <Show when={props.compact && props.controller.plannerDegraded()}>
        <span class="ual-search-badge is-warning">规划降级 · 已继续检索</span>
      </Show>
      <Index each={queries()}>{(item, index) => <div class="ual-search-query-row">
        <select value={item().media_type || "image"} onInput={(event) => props.controller.updatePlanQuery(index, { media_type: event.currentTarget.value })}>
          <For each={MEDIA_TYPES}>{(type) => <option value={type.key}>{type.label}</option>}</For>
        </select>
        <input value={item().query || ""} onInput={(event) => props.controller.updatePlanQuery(index, { query: event.currentTarget.value })} />
        <button type="button" title="Remove query" onClick={() => props.controller.removePlanQuery(index)}>
          <FlowIcon name="close" />
        </button>
      </div>}</Index>
      <button class="ual-search-add-query" type="button" onClick={() => props.controller.addPlanQuery()}>
        <FlowIcon name="add" /> 添加关键词
      </button>
    </div>
  </section>;
}

export function SearchSourceFilters(props) {
  const activeSources = () => props.controller.sources();
  const activeTypes = () => props.controller.mediaTypes();
  return <section class={`ual-search-filters ${props.compact ? "is-compact" : ""}`}>
    <div class="ual-search-filter-row">
      <Show when={!props.compact}><span>Sources</span></Show>
      <div>
        <For each={PROVIDERS}>{(item) => <ProviderBadge
          item={item}
          active={activeSources().has(item.key)}
          providerStatus={props.controller.providerStatus()}
          status={props.controller.providerEvents()}
          onClick={() => props.controller.toggleSource(item.key)}
        />}</For>
      </div>
    </div>
    <div class="ual-search-filter-row">
      <Show when={!props.compact}><span>Type</span></Show>
      <div>
        <For each={MEDIA_TYPES}>{(item) => <button class={`ual-search-chip ${activeTypes().has(item.key) ? "is-active" : ""}`} type="button" onClick={() => props.controller.toggleMediaType(item.key)}>{item.label}</button>}</For>
      </div>
    </div>
    <div class="ual-search-filter-row">
      <Show when={!props.compact}><span>Aspect</span></Show>
      <div>
        <For each={ASPECTS}>{(item) => <button class={`ual-search-chip ${props.controller.aspect() === item.key ? "is-active" : ""}`} type="button" onClick={() => props.controller.setAspect(item.key)}>{item.label}</button>}</For>
      </div>
    </div>
    <div class="ual-search-filter-row">
      <Show when={!props.compact}><span>Limit</span></Show>
      <div>
        <input class="ual-search-limit-input" type="number" min="1" max="24" value={props.controller.limitPerSource()} onInput={(event) => props.controller.setLimitPerSource(event.currentTarget.value)} />
      </div>
    </div>
  </section>;
}

function CandidatePreview(props) {
  const candidate = () => props.candidate;
  const imageSrc = () => candidate()?.preview_url || candidate()?.download_url || candidate()?.thumbnail_url;
  const displayAspect = () => props.displayAspect || candidateDisplayAspect(candidate(), props.aspect?.());
  return <div class={`ual-search-preview is-${displayAspect()}`}>
    <Show when={candidateMediaType(candidate()) === "video"} fallback={
      <Show when={candidateMediaType(candidate()) === "audio"} fallback={<img src={imageSrc()} alt="" loading="lazy" />}>
        <div class="ual-search-audio-preview"><FlowIcon name="audio" /><audio src={candidate()?.preview_url || candidate()?.download_url} controls preload="none" /></div>
      </Show>
    }>
      <video src={candidate()?.preview_url || candidate()?.download_url} poster={candidate()?.preview_url || candidate()?.thumbnail_url} muted playsinline preload="none" />
    </Show>
  </div>;
}

export function SearchAgentSettings(props) {
  const draft = () => props.controller.apiKeyDraft();
  return <section class="ual-search-settings">
    <div class="ual-search-section-title">
      <strong>Provider Settings</strong>
      <span>keys stay server-side</span>
    </div>
    <div class="ual-search-key-grid">
      <For each={PROVIDERS.filter((item) => !item.keyless)}>{(provider) => <label>
        <span>{provider.label} {props.controller.providerStatus()?.[provider.key]?.configured ? "configured" : "no key"}</span>
        <input type="password" autoComplete="off" value={draft()[provider.key] || ""} placeholder="Paste key to update" onInput={(event) => props.controller.updateApiKeyDraft(provider.key, event.currentTarget.value)} />
      </label>}</For>
      <button type="button" disabled={props.controller.settingsBusy() || !Object.values(draft()).some((value) => String(value || "").trim())} onClick={() => props.controller.saveProviderKeys()}>
        <FlowIcon name="download" /> 保存凭据
      </button>
    </div>
  </section>;
}

function SearchCandidateCard(props) {
  let menuButtonEl;
  const candidate = () => props.candidate;
  const key = () => candidateKey(candidate());
  const selected = () => props.selectedIds.has(key());
  const disabled = () => !candidateSupportsImport(candidate());
  const displayAspect = () => candidateDisplayAspect(candidate(), props.aspect?.());
  const firstFragment = () => asArray(candidate()?.matched_fragments)[0] || null;
  const [menuOpen, setMenuOpen] = createSignal(false);
  const stop = (event) => event.stopPropagation();
  const viewDetail = (event) => {
    stop(event);
    setMenuOpen(false);
    props.onFocus(candidate());
  };
  const toggleImport = (event) => {
    stop(event);
    props.onToggle(candidate());
  };
  const openSource = (event) => {
    stop(event);
    setMenuOpen(false);
    const href = candidate()?.source_url;
    if (!href) return;
    window.open(href, "_blank", "noreferrer");
  };
  const deleteCandidate = (event) => {
    stop(event);
    setMenuOpen(false);
    props.onDelete?.(candidate());
  };
  const openEditor = (event) => {
    stop(event);
    setMenuOpen(false);
    const item = candidate();
    const fragment = firstFragment();
    const hash = buildMediaLibraryEditorHash({
      assetId: item?.asset_id,
      startMs: fragment?.start_ms,
      endMs: fragment?.end_ms,
      searchId: item?.media_library_search_id,
      matchedFragmentId: fragment?.fragment_id,
    });
    if (hash) window.location.hash = hash;
  };
  return <article class={`ual-search-card is-${displayAspect()} ${selected() ? "is-selected" : ""} ${disabled() ? "is-disabled" : ""} ${menuOpen() ? "is-menu-open" : ""}`} title={candidateTitle(candidate())}>
    <CandidatePreview candidate={candidate()} displayAspect={displayAspect()} />
    <Show when={firstFragment()}>
      <div class="ual-search-card-match">
        <strong>{mediaLibraryFragmentKindLabel(firstFragment())}</strong>
        <span>{formatSearchRange(firstFragment().start_ms, firstFragment().end_ms)}</span>
      </div>
    </Show>
    <span class={`ual-search-source-label is-${candidate()?.provider || candidate()?.source || "unknown"}`}>{assetSearchSourceLabel(candidate()?.provider || candidate()?.source)}</span>
    <div class="ual-search-card-actions">
      <button type="button" title={selected() ? "取消导入选择" : "选择导入"} aria-pressed={selected()} disabled={disabled()} onClick={toggleImport}>
        <FlowIcon name={selected() ? "close" : "add"} />
      </button>
      <button ref={(el) => { menuButtonEl = el; }} type="button" title="More" aria-expanded={menuOpen()} onClick={(event) => {
        stop(event);
        setMenuOpen((value) => !value);
      }}>
        <FlowIcon name="moreVert" />
      </button>
    </div>
    {menuOpen() ? <FloatingAssetMenu anchor={() => menuButtonEl} onClose={() => setMenuOpen(false)} onClick={stop}>
      <button type="button" role="menuitem" onClick={viewDetail}><FlowIcon name="image" />查看详情</button>
      <Show when={candidateSupportsAction(candidate(), "open_editor") && candidate()?.asset_id && firstFragment()}>
        <button type="button" role="menuitem" onClick={openEditor}><FlowIcon name="cut" />剪切首个命中范围</button>
      </Show>
      <button type="button" role="menuitem" disabled={!candidate()?.source_url} onClick={openSource}><FlowIcon name="share" />Source</button>
      <hr />
      <button type="button" role="menuitem" class="is-danger" onClick={deleteCandidate}><FlowIcon name="delete" />删除</button>
    </FloatingAssetMenu> : null}
  </article>;
}

function CandidateDetailModal(props) {
  const candidate = () => props.candidate;
  const fragments = () => asArray(candidate()?.matched_fragments);
  const openEditor = (fragment) => {
    const item = candidate();
    const hash = buildMediaLibraryEditorHash({
      assetId: item?.asset_id,
      startMs: fragment?.start_ms,
      endMs: fragment?.end_ms,
      searchId: item?.media_library_search_id,
      matchedFragmentId: fragment?.fragment_id,
    });
    if (hash) window.location.hash = hash;
  };
  return <Show when={candidate()}>
    <div class="ual-search-detail-backdrop" onClick={props.onClose} />
    <section class="ual-search-detail-modal" role="dialog" aria-modal="true" aria-label="候选素材详情">
      <header>
        <div>
          <strong>{candidateTitle(candidate())}</strong>
          <span>{candidateMeta(candidate())}</span>
        </div>
        <button type="button" class="ual-agent-icon" aria-label="Close" title="Close" onClick={props.onClose}>
          <FlowIcon name="close" />
        </button>
      </header>
      <div class="ual-search-detail-modal-body">
        <CandidatePreview candidate={candidate()} displayAspect={candidateDisplayAspect(candidate())} />
        <div class="ual-search-detail-content">
          <p>{candidate()?.description || candidateMeta(candidate())}</p>
          <dl>
            <dt>Title</dt><dd>{candidateTitle(candidate())}</dd>
            <dt>Provider</dt><dd>{assetSearchSourceLabel(candidate()?.provider || candidate()?.source)}</dd>
            <Show when={candidate()?.provider === "media_library" || candidate()?.source === "media_library"}>
              <dt>类型</dt><dd>{candidate()?.candidate_kind === "derived_clip" ? "可复用派生片段" : "原视频"}</dd>
            </Show>
            <dt>Media</dt><dd>{candidateMeta(candidate()) || candidateMediaType(candidate())}</dd>
            <dt>Creator</dt><dd>{candidate()?.creator?.name || "Unknown"}</dd>
            <dt>License</dt><dd>{candidate()?.license?.name || "Unknown"}</dd>
            <dt>Status</dt><dd>{candidate()?.license?.license_status || "Unknown"}</dd>
            <dt>Attribution</dt><dd>{candidate()?.license?.attribution_text || "None"}</dd>
            <dt>Import</dt><dd>{candidateSupportsImport(candidate()) ? "可选择导入" : (candidate()?.import_unsupported_reason || "Not supported")}</dd>
          </dl>
          <Show when={candidate()?.candidate_kind === "derived_clip" && asArray(candidate()?.tags).length}>
            <p>标签：{asArray(candidate()?.tags).join("、")}</p>
          </Show>
          <div class="ual-search-reasons">
            <For each={asArray(candidate()?.score_reasons)}>{(item) => <span>{item}</span>}</For>
          </div>
          <Show when={fragments().length}>
            <div class="ual-search-match-list">
              <strong>命中片段</strong>
              <For each={fragments()}>{(fragment) => <section>
                <div>
                  <b>{mediaLibraryFragmentKindLabel(fragment)}</b>
                  <span>{formatSearchRange(fragment.start_ms, fragment.end_ms)}</span>
                </div>
                <p>{fragment.dialogue_text || fragment.summary || "已返回可解释的命中范围"}</p>
                <Show when={candidateSupportsAction(candidate(), "open_editor") && candidate()?.asset_id}>
                  <button type="button" onClick={() => openEditor(fragment)}>以此范围打开剪辑</button>
                </Show>
              </section>}</For>
            </div>
          </Show>
        </div>
      </div>
    </section>
  </Show>;
}

function SearchImportTray(props) {
  const selected = () => props.controller.selectedCandidates();
  const byProvider = () => selected().reduce((acc, item) => {
    const key = item.provider || item.source || "unknown";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  return <section class="ual-search-import-tray">
    <div>
      <strong>Selected to Import</strong>
      <span>{selected().length} / {MAX_IMPORT_SELECTION} candidates</span>
    </div>
    <div class="ual-search-import-actions">
      <button type="button" onClick={() => props.controller.exportSourceList()}>
        导出来源清单
      </button>
      <button type="button" disabled={!selected().length || selected().length > MAX_IMPORT_SELECTION || props.controller.importBusy()} onClick={() => props.controller.setConfirmImportOpen(true)}>
        导入
      </button>
    </div>
    <Show when={props.controller.confirmImportOpen()}>
      <div class="ual-search-confirm" role="alertdialog" aria-modal="true" aria-label="Confirm asset import">
        <div>
          <strong>将 {selected().length} 个素材导入或复用到当前 Asset Library</strong>
          <For each={Object.entries(byProvider())}>{([provider, count]) => <p>{assetSearchSourceLabel(provider)}: {count}</p>}</For>
          <small>外部素材按既有授权链路下载；当前 Task 素材直接复用；全局素材库原视频或已加入检索的派生片段由后端读取权威记录并安全复制。系统不会自动修改已有 StoryBoard 素材。</small>
        </div>
        <div>
          <button type="button" onClick={() => props.controller.setConfirmImportOpen(false)}>取消</button>
          <button type="button" disabled={props.controller.importBusy() || selected().length > MAX_IMPORT_SELECTION} onClick={() => props.controller.importSelected()}>确认导入</button>
        </div>
      </div>
    </Show>
  </section>;
}

function ImportResults(props) {
  return <Show when={props.controller.importedAssets().length || props.controller.failedImports().length || props.controller.sourceExport()}>
    <section class="ual-search-import-results">
      <strong>Imported Results</strong>
      <For each={props.controller.importedAssets()}>{(item) => <p class="is-success">{item.skipped ? "已存在，复用" : "已导入"} · {item.filename || item.path}</p>}</For>
      <For each={props.controller.failedImports()}>{(item) => <p class="is-error">失败 · {item.candidate_id}: {item.reason}</p>}</For>
      <Show when={props.controller.sourceExport()}>
        <p class="is-success">来源清单 · {props.controller.sourceExport()?.markdown_path}</p>
      </Show>
    </section>
  </Show>;
}

function SearchEmptyState(props) {
  const completedProviders = () => Object.entries(props.controller.providerEvents() || {})
    .filter(([, state]) => state?.status && state.status !== "searching");
  const providerSummary = () => completedProviders()
    .map(([provider, state]) => {
      const label = PROVIDERS.find((item) => item.key === provider)?.label || provider;
      const status = state.status === "ok" ? `${state.kept || 0} kept` : state.status;
      return `${label}: ${status}`;
    })
    .join(" / ");
  const wikimediaEmpty = () => completedProviders()
    .some(([provider, state]) => provider === "wikimedia" && state.status === "ok" && Number(state.kept || 0) === 0);
  const searchedGlobalLibrary = () => props.controller.sources().has("media_library")
    && (Boolean(props.controller.searchId()) || completedProviders().some(([provider]) => provider === "media_library"));
  return <div class="ual-search-empty">
    <strong>{props.controller.phase() === "searching" ? "等待候选素材" : "暂无候选素材"}</strong>
    <Show when={providerSummary()}>
      <p>{providerSummary()}</p>
    </Show>
    <Show when={wikimediaEmpty()}>
      <p>Wikimedia 对长描述和 stock/b-roll 词不敏感，可尝试 hospital corridor、medical staff、doctor hospital 这类短关键词。</p>
    </Show>
    <Show when={searchedGlobalLibrary() && props.controller.phase() !== "searching"}>
      <p>全局素材库不会自动放宽原视频分析资格或派生片段显式加入检索的条件。可以尝试：</p>
      <ul><For each={MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS}>{(item) => <li>{item}</li>}</For></ul>
    </Show>
  </div>;
}

export default function SearchAgentWorkspace(props) {
  const controller = props.controller;
  const [detailCandidate, setDetailCandidate] = createSignal(null);
  onMount(() => {
    controller.ensureSettings().catch((err) => emitDebugError(err, {
      family: "asset_search_settings",
      task_id: props.task?.()?.id || null,
      detail: "Load asset-search settings failed",
    }));
  });
  return <div class="ual-search-workspace">
    <div class="ual-search-capability-note">
      <strong>全局素材库能力边界</strong>
      <span>{MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT}</span>
    </div>
    <Show when={controller.plannerDegraded()}>
      <div class="ual-search-planner-degraded">查询规划暂时不可用，系统已使用原始关键词继续确定性检索；候选与导入操作仍然可用。</div>
    </Show>
    <div class="ual-search-results-layout">
      <section class="ual-search-candidates">
        <div class="ual-search-section-title">
          <strong>Candidate Results</strong>
          <span>{controller.candidates().length}</span>
        </div>
        <Show when={controller.candidates().length} fallback={<SearchEmptyState controller={controller} />}>
          <div class="ual-search-grid" style={{ "--ual-image-columns": String(props.imageColumns?.() || 6) }}>
            <For each={controller.candidates()}>{(candidate) => <SearchCandidateCard
              candidate={candidate}
              aspect={controller.aspect}
              selectedIds={controller.selectedIds()}
              onToggle={controller.toggleCandidate}
              onDelete={(item) => {
                controller.removeCandidate(item);
                if (candidateKey(detailCandidate()) === candidateKey(item)) setDetailCandidate(null);
              }}
              onFocus={(item) => {
                controller.setActiveCandidateId(candidateKey(item));
                setDetailCandidate(item);
              }}
            />}</For>
          </div>
        </Show>
      </section>
    </div>
    <CandidateDetailModal candidate={detailCandidate()} onClose={() => setDetailCandidate(null)} />
    <SearchImportTray controller={controller} />
    <ImportResults controller={controller} />
  </div>;
}
