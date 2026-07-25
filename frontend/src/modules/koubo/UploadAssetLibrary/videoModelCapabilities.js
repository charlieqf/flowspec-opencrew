import { assetKind } from "./uploadAssetLibraryModel.js";

function text(value) {
  return String(value || "").trim();
}

function intValue(value, fallback = 0) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function uniqNumbers(values = []) {
  const seen = new Set();
  return values
    .map((value) => Number.parseInt(String(value), 10))
    .filter((value) => Number.isFinite(value) && value > 0)
    .filter((value) => {
      if (seen.has(value)) return false;
      seen.add(value);
      return true;
    });
}

export function normalizeVideoAlias(value) {
  return text(value).replace(/[\s_.-]+/g, "").toLowerCase();
}

function videoAgentModelsFor(config = {}) {
  return Array.isArray(config?.agent_model_aliases) ? config.agent_model_aliases : [];
}

function catalogModelsFor(config = {}) {
  const providers = Array.isArray(config?.providers) ? config.providers : [];
  return providers.flatMap((provider) => {
    const models = Array.isArray(provider?.models) ? provider.models : [];
    return models.map((model) => ({
      ...model,
      provider: text(model.provider || provider.provider || provider.id || provider.provider_id),
      provider_label: text(provider.name || provider.label || provider.provider || provider.id),
    }));
  });
}

function selectedAlias(config = {}, source = {}) {
  const alias = text(source.agentVideoAlias || source.agent_video_alias);
  const provider = text(source.provider);
  const model = text(source.model);
  const aliases = videoAgentModelsFor(config);
  if (alias) return aliases.find((item) => text(item.alias) === alias) || null;
  if (provider && model) {
    return aliases.find((item) => text(item.provider) === provider && text(item.model) === model) || null;
  }
  return null;
}

function aliasCapability(item = {}) {
  const value = item?.capability || item?.model_capability || item?.modelCapabilities;
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}

function selectedCatalogModel(config = {}, selected = {}, source = {}) {
  const capability = aliasCapability(selected);
  if (capability) return capability;
  const provider = text(selected?.provider || source.provider);
  const model = text(selected?.model || source.model);
  if (!model) return null;
  const catalog = catalogModelsFor(config);
  return (provider ? catalog.find((item) => text(item.provider) === provider && text(item.model || item.id) === model) : null)
    || catalog.find((item) => text(item.model || item.id) === model)
    || null;
}

export function videoAgentModelSupportsText(config = {}, item = {}) {
  const model = selectedCatalogModel(config, item, item) || {};
  const tasks = Array.isArray(model.tasks) ? model.tasks.map((value) => text(value).toLowerCase()) : [];
  if (tasks.length) return tasks.includes("text_to_video");
  const inputModes = Array.isArray(model.input_modes) ? model.input_modes.map((value) => text(value).toLowerCase()) : [];
  if (inputModes.length) return inputModes.includes("text");
  const capabilities = Array.isArray(model.capabilities) ? model.capabilities.map((value) => text(value).toLowerCase()) : [];
  return capabilities.includes("text") || capabilities.includes("text_to_video") || capabilities.includes("text-to-video");
}

function readReferenceLimit(model = {}, key, fallback = 0) {
  const value = model?.model_options?.[key] || model?.model_option?.[key] || model?.[key] || {};
  return {
    min: Math.max(0, intValue(value.min ?? value.minimum, 0)),
    max: Math.max(0, intValue(value.max ?? value.maximum, fallback)),
  };
}

function readDuration(model = {}) {
  const duration = model?.model_options?.duration || model?.model_option?.duration || model?.duration || {};
  const values = uniqNumbers(duration.values || duration.options || duration.presets || duration.allowed || duration.enum || []);
  const min = intValue(duration.min ?? duration.minimum, values.length ? Math.min(...values) : 4);
  const max = intValue(duration.max ?? duration.maximum, values.length ? Math.max(...values) : Math.max(min, 15));
  return {
    min: Math.max(1, min),
    max: Math.max(Math.max(1, min), max),
    presets: values,
  };
}

function runtimeReferenceMode(model = {}) {
  const mode = text(
    model.reference_mode
    || model.referenceMode
    || model.reference_images?.mode
    || model.referenceImages?.mode,
  ).toLowerCase();
  if (mode === "first_frame") return "first_frame";
  if (["input_references", "reference_to_video", "sr2"].includes(mode)) return "input_references";
  return "";
}

function baseCapability(source = {}, config = {}, options = {}) {
  const selected = selectedAlias(config, source);
  const model = selectedCatalogModel(config, selected, source) || {};
  const aliasKey = normalizeVideoAlias(selected?.alias || source.agentVideoAlias || source.agent_video_alias);
  const provider = text(selected?.provider || source.provider);
  const modelID = text(selected?.model || source.model);
  const imageLimit = readReferenceLimit(model, "reference_images", 4);
  const audioLimit = readReferenceLimit(model, "reference_audios", 0);
  const videoLimit = readReferenceLimit(model, "reference_videos", 0);
  const duration = readDuration(model);
  const tasks = Array.isArray(model.tasks) ? model.tasks.map((value) => text(value).toLowerCase()).filter(Boolean) : [];
  const aspectRatios = Array.isArray(model.aspect_ratios)
    ? model.aspect_ratios.map((value) => text(value)).filter((value) => ["9:16", "16:9"].includes(value))
    : [];
  const countValues = options.isAgent ? [1] : [1, 2];
  return {
    alias: text(selected?.alias || source.agentVideoAlias || source.agent_video_alias),
    aliasKey,
    provider,
    model: modelID,
    label: text(selected?.label || selected?.alias || selected?.model || model?.label || model?.name || modelID || "视频模型"),
    referenceMode: runtimeReferenceMode(model),
    tasks,
    statefulEdit: model.stateful_edit === true,
    providerState: text(model.provider_state),
    supportsVideoInput: model.supports_video_input === true,
    supportsAudioInput: model.supports_audio_input === true,
    references: {
      images: { ...imageLimit, label: "图片" },
      audios: { ...audioLimit, label: "音频" },
      videos: { ...videoLimit, label: "视频" },
      totalMin: Math.max(0, imageLimit.min) + Math.max(0, audioLimit.min) + Math.max(0, videoLimit.min),
      totalMax: Math.max(0, imageLimit.max) + Math.max(0, audioLimit.max) + Math.max(0, videoLimit.max),
    },
    params: {
      aspect: { values: aspectRatios.length ? aspectRatios : ["9:16", "16:9"] },
      duration,
      count: { values: countValues, enabled: countValues.length > 1 },
    },
  };
}

function patchCapability(capability, options = {}) {
  const aliasKey = capability.aliasKey;
  const count = options.isAgent ? { values: [1], enabled: false } : { values: [1, 2], enabled: true };
  const next = {
    ...capability,
    references: {
      ...capability.references,
      images: { ...capability.references.images },
      audios: { ...capability.references.audios },
      videos: { ...capability.references.videos },
    },
    params: {
      ...capability.params,
      duration: { ...capability.params.duration },
      count,
    },
  };
  if (capability.statefulEdit) {
    next.params.count = { values: [1], enabled: false };
  }
  if (aliasKey === "maxsi2") {
    next.referenceMode = "first_frame";
    next.references.images = { min: 0, max: 1, label: "首帧" };
    next.references.audios = { min: 0, max: 0, label: "音频" };
    next.references.videos = { min: 0, max: 0, label: "视频" };
  } else if (aliasKey === "maxhr10" || aliasKey === "happyhorse10" || aliasKey === "happyhorse1") {
    next.referenceMode = "";
    next.references.images = { min: 1, max: 3, label: "图片" };
    next.references.audios = { min: 0, max: 0, label: "音频" };
    next.references.videos = { min: 0, max: 0, label: "视频" };
    next.params.duration = { min: 3, max: 15, presets: [] };
  } else if (aliasKey === "maxwr27" || aliasKey === "wan27") {
    next.referenceMode = "";
    next.references.images = { min: 0, max: 5, label: "参考图" };
    next.references.audios = { min: 0, max: 0, label: "音频" };
    next.references.videos = { min: 0, max: 5, label: "参考视频" };
  } else if (aliasKey === "maxsr2") {
    next.referenceMode = "input_references";
    next.references.images = { min: 0, max: 8, label: "图片" };
    next.references.audios = { min: 0, max: 4, label: "音频" };
    next.references.videos = { min: 0, max: 4, label: "视频" };
  }
  next.references.totalMin = aliasKey === "maxwr27" || aliasKey === "wan27"
    ? 1
    : next.references.images.min + next.references.audios.min + next.references.videos.min;
  next.references.totalMax = aliasKey === "maxwr27" || aliasKey === "wan27"
    ? 5
    : next.references.images.max + next.references.audios.max + next.references.videos.max;
  return next;
}

export function resolveVideoModelCapability(source = {}, config = {}, options = {}) {
  return patchCapability(baseCapability(source, config, options), options);
}

export function videoCapabilitySupportsTask(capability = {}, task = "") {
  const tasks = Array.isArray(capability.tasks) ? capability.tasks : [];
  return tasks.includes(text(task).toLowerCase());
}

export function isStatefulVideoCapability(capability = {}) {
  return capability.statefulEdit === true
    && capability.providerState === "interaction"
    && videoCapabilitySupportsTask(capability, "edit");
}

export function referencesToVideoSlots(items = []) {
  const groups = { images: [], audios: [], videos: [] };
  const seen = new Set();
  for (const item of items || []) {
    const key = text(item?.path || item?.id);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const kind = assetKind(item);
    if (kind === "image") groups.images.push(item);
    else if (kind === "audio") groups.audios.push(item);
    else if (kind === "video") groups.videos.push(item);
  }
  return groups;
}

function capError(count, cap) {
  if (cap.max <= 0 && count > 0) return `${cap.label} are not supported by this video model.`;
  if (count < cap.min) return `${cap.label} require at least ${cap.min}.`;
  if (count > cap.max) return `${cap.label} allow at most ${cap.max}.`;
  return "";
}

export function validateVideoGenerationInputs(capability, slots) {
  const images = slots?.images?.length || 0;
  const audios = slots?.audios?.length || 0;
  const videos = slots?.videos?.length || 0;
  const errors = [
    capError(images, capability.references.images),
    capError(audios, capability.references.audios),
    capError(videos, capability.references.videos),
  ].filter(Boolean);
  const total = images + audios + videos;
  if (total < (capability.references.totalMin || 0)) {
    errors.push(`References require at least ${capability.references.totalMin} total assets.`);
  }
  if (total > capability.references.totalMax) {
    errors.push(`References allow at most ${capability.references.totalMax} total assets.`);
  }
  return {
    ok: errors.length === 0,
    errors,
    counts: { images, audios, videos, total },
    limits: capability.references,
  };
}
