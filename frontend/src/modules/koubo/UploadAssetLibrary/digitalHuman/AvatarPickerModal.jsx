import { For, Show, createEffect, createSignal } from "solid-js";
import FlowIcon from "../components/FlowIcon.jsx";
import { dataItems, normalizeAvatar, text } from "./digitalHumanModel.js";

const DEFAULT_MOTION_PROMPT = "Use subtle hand gestures and natural body movement.";
const EXPRESSIVENESS_OPTIONS = ["low", "medium", "high"];
const DEFAULT_AVATAR_MODEL_NAME = "Avatar IV";

function avatarShape(item = {}) {
  const width = Number(item.raw?.image_width || item.image_width || 0);
  const height = Number(item.raw?.image_height || item.image_height || 0);
  if (width && height) return width / height > 1.15 ? "landscape" : "portrait";
  return "portrait";
}

function isAvatarReady(item = {}) {
  const status = text(item.status || "completed").toLowerCase();
  return status === "completed" || status === "complete" || status === "ready";
}

export default function AvatarPickerModal(props) {
  let fileInput;
  const defaultDescription = "Photo avatar generated from Asset Library image.";
  const [tab, setTab] = createSignal("select");
  const [avatarType, setAvatarType] = createSignal("");
  const [items, setItems] = createSignal([]);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [name, setName] = createSignal("");
  const [description, setDescription] = createSignal(defaultDescription);
  const [uploadedImage, setUploadedImage] = createSignal(null);
  const [uploading, setUploading] = createSignal(false);
  const [creating, setCreating] = createSignal(false);
  const [deletingIds, setDeletingIds] = createSignal(new Set());
  const selectedImage = () => uploadedImage() || props.selectedImage?.() || null;
  const selectedImagePath = () => selectedImage()?.path || "";
  const engineType = () => text(props.settings?.()?.engine_type, "avatar_iv").toLowerCase();
  const modelName = () => text(props.settings?.()?.model_name, engineType() === "avatar_v" ? "Avatar V" : engineType() === "video_agent" ? "Video Agent" : DEFAULT_AVATAR_MODEL_NAME);
  const motionPromptEnabled = () => props.settings?.()?.motion_prompt_enabled === true;
  const motionPrompt = () => text(props.settings?.()?.motion_prompt);
  const expressivenessEnabled = () => engineType() === "avatar_iv";
  const expressiveness = () => {
    const value = text(props.settings?.()?.expressiveness, "low").toLowerCase();
    return EXPRESSIVENESS_OPTIONS.includes(value) ? value : "low";
  };

  function updateAvatarMotion(next = {}) {
    props.setSettings?.((current) => ({
      ...current,
      motion_prompt_enabled: next.motion_prompt_enabled !== undefined ? next.motion_prompt_enabled : current.motion_prompt_enabled,
      motion_prompt: next.motion_prompt !== undefined ? next.motion_prompt : current.motion_prompt,
      expressiveness: next.expressiveness !== undefined ? next.expressiveness : current.expressiveness,
      model_name: current.model_name || DEFAULT_AVATAR_MODEL_NAME,
      engine_type: current.engine_type || "avatar_iv",
    }));
  }

  function avatarDeleting(item = {}) {
    return deletingIds().has(text(item.id));
  }

  const avatarMotionControls = () => (
    <section class="dh-avatar-motion">
      <label>动作描述<select value={motionPromptEnabled() ? "custom" : "none"} onChange={(event) => updateAvatarMotion({
        motion_prompt_enabled: event.currentTarget.value === "custom",
        motion_prompt: event.currentTarget.value === "custom" && !motionPrompt() ? DEFAULT_MOTION_PROMPT : motionPrompt(),
      })}>
        <option value="none">不发送动作描述</option>
        <option value="custom">自定义动作描述</option>
      </select></label>
      <label>动作幅度<select disabled={!expressivenessEnabled()} value={expressivenessEnabled() ? expressiveness() : engineType()} onChange={(event) => updateAvatarMotion({ expressiveness: event.currentTarget.value })}>
        <Show when={!expressivenessEnabled()}><option value={engineType()}>{modelName()} 不发送</option></Show>
        <Show when={expressivenessEnabled()}><For each={EXPRESSIVENESS_OPTIONS}>{(value) => <option value={value}>{value}</option>}</For></Show>
      </select></label>
      <Show when={motionPromptEnabled()}>
        <label class="dh-motion-prompt-field">动作描述内容<textarea value={motionPrompt()} onInput={(event) => updateAvatarMotion({ motion_prompt: event.currentTarget.value })} placeholder={DEFAULT_MOTION_PROMPT} rows="3" /></label>
      </Show>
    </section>
  );

  async function loadAvatars() {
    if (!props.open?.() || !props.taskId?.()) return;
    setLoading(true);
    setError("");
    try {
      const payload = await props.api.assetLibraryDigitalHumanAvatars(props.taskId(), {
        ownership: "private",
        avatar_type: avatarType(),
        limit: 30,
      });
      setItems(dataItems(payload).map(normalizeAvatar).filter((item) => item.id));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message === "Not Found"
        ? "数字人形象后端路由未加载。请重启 OpenCrew backend 后再刷新；重启后这里会调用云端形象列表接口。"
        : message);
    } finally {
      setLoading(false);
    }
  }

  createEffect(() => {
    if (props.open?.() && tab() === "select") void loadAvatars();
  });

  async function createAvatar() {
    const avatarName = text(name(), selectedImage()?.label || selectedImage()?.filename || "Photo Avatar");
    if (!avatarName) return;
    if (!selectedImagePath()) {
      setError("请先上传照片或点击左侧 Asset Library Images 中的一张图片。");
      return;
    }
    setCreating(true);
    setError("");
    try {
      const result = await props.api.createAssetLibraryDigitalHumanPhotoAvatar(props.taskId(), {
        name: avatarName,
        description: text(description(), defaultDescription),
        assetPath: selectedImagePath(),
      });
      const avatar = normalizeAvatar(result?.result?.data?.avatar_item || result?.result?.avatar_item || result?.result?.data || {});
      props.onSelect?.(avatar);
      props.onClose?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  async function deleteAvatar(item, event) {
    event?.stopPropagation?.();
    const avatarId = text(item?.id);
    if (!avatarId || avatarDeleting(item)) return;
    const label = text(item?.name, avatarId);
    if (!window.confirm(`删除形象「${label}」？删除后会从云端账号中移除，不能用于后续生成。`)) return;
    setDeletingIds((current) => new Set(current).add(avatarId));
    setError("");
    try {
      await props.api.deleteAssetLibraryDigitalHumanAvatar(props.taskId(), avatarId);
      setItems((current) => current.filter((avatar) => text(avatar.id) !== avatarId));
      if (text(props.settings?.()?.selected_avatar_id) === avatarId) props.onSelect?.(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(avatarId);
        return next;
      });
    }
  }

  async function uploadAvatarPhoto(files) {
    const photo = Array.from(files || []).find((item) => item);
    if (!photo) return;
    setUploading(true);
    setError("");
    try {
      const added = await props.uploadImageFiles?.([photo], { source: "digital_human_avatar_photo" });
      const image = (added || []).find((item) => item?.path);
      if (!image) throw new Error("照片已上传，但没有返回 Asset Library image 路径。");
      setUploadedImage(image);
      props.onSelectImage?.(image);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  return <Show when={props.open?.()}>
    <div class="dh-modal-backdrop" role="presentation" onClick={() => props.onClose?.()}>
      <section class="dh-settings dh-picker" role="dialog" aria-label="Avatar" onClick={(event) => event.stopPropagation()}>
        <header class="dh-modal-head">
          <button type="button" onClick={() => props.onClose?.()} aria-label="Back"><FlowIcon name="arrowBack" /></button>
          <strong>Avatar</strong>
          <button type="button" onClick={() => props.onClose?.()} aria-label="Close"><FlowIcon name="close" /></button>
        </header>
        <div class="dh-settings-body dh-picker-body">
          <div class="dh-tabs">
            <button class={tab() === "select" ? "is-active" : ""} type="button" onClick={() => setTab("select")}>选择 Avatar</button>
            <button class={tab() === "create" ? "is-active" : ""} type="button" onClick={() => setTab("create")}>上传照片生成</button>
          </div>
          <Show when={error()}><div class="dh-error">{error()}</div></Show>
          <Show when={tab() === "select"} fallback={
            <div class="dh-create-form">
              <label>名称<input value={name()} onInput={(event) => setName(event.currentTarget.value)} placeholder="Avatar name" /></label>
              <label>描述<textarea value={description()} onInput={(event) => setDescription(event.currentTarget.value)} placeholder={defaultDescription} rows="3" /></label>
              {avatarMotionControls()}
              <section class="dh-selected-image-panel">
                <strong>Asset Library Image</strong>
                <Show when={selectedImage()} fallback={<div class="dh-selected-image-empty">点击左侧 Images 中的一张图片作为 Avatar 源图</div>}>
                  {(asset) => <div class="dh-selected-image-card">
                    <img src={props.assetUrl?.(asset())} alt="" />
                  </div>}
                </Show>
              </section>
              <input ref={fileInput} type="file" accept="image/*,.png,.jpg,.jpeg,.webp" hidden onChange={(event) => {
                void uploadAvatarPhoto(event.currentTarget.files);
                event.currentTarget.value = "";
              }} />
              <button type="button" class="dh-secondary" disabled={uploading()} onClick={() => fileInput?.click()}><FlowIcon name="image" />{uploading() ? "上传中" : "上传照片"}</button>
              <button type="button" class="dh-primary" disabled={creating() || uploading()} onClick={() => void createAvatar()}>{creating() ? "生成中" : "生成并选择 Avatar"}</button>
            </div>
          }>
            {avatarMotionControls()}
            <div class="dh-filter-row">
              <select value={avatarType()} onChange={(event) => setAvatarType(event.currentTarget.value)}>
                <option value="">全部类型</option>
                <option value="photo_avatar">Photo Avatar</option>
                <option value="digital_twin">Digital Twin</option>
                <option value="studio_avatar">Studio Avatar</option>
              </select>
              <button type="button" onClick={() => void loadAvatars()} disabled={loading()}>{loading() ? "Loading" : "Refresh"}</button>
            </div>
            <Show when={!loading()} fallback={<div class="dh-empty">Loading avatars...</div>}>
              <Show when={items().length} fallback={<div class="dh-empty">没有可用 Avatar</div>}>
                <div class="dh-card-grid">
                  <For each={items()}>{(item) => (
                    <article class={`dh-pick-card is-${avatarShape(item)} ${!isAvatarReady(item) ? "is-disabled" : ""}`}>
                      <div class="dh-card-preview">
                        <Show when={item.preview_image_url || item.source_path} fallback={<FlowIcon name="image" />}>
                          <img src={item.preview_image_url || props.assetUrl?.({ path: item.source_path })} alt="" />
                        </Show>
                      </div>
                      <Show when={!isAvatarReady(item)}>
                        <span class="dh-status-badge">{item.status || "processing"}</span>
                      </Show>
                      <div class="dh-card-hover">
                        <strong>{item.name}</strong>
                        <span>{item.avatar_type || "avatar"} · {item.status || "completed"}</span>
                        <div class="dh-card-actions">
                          <button type="button" disabled={!isAvatarReady(item) || avatarDeleting(item)} onClick={() => { props.onSelect?.(item); props.onClose?.(); }}>{isAvatarReady(item) ? "选择" : "处理中"}</button>
                          <button type="button" class="is-danger" disabled={avatarDeleting(item)} title="删除 Avatar" aria-label="删除 Avatar" onClick={(event) => void deleteAvatar(item, event)}>
                            <FlowIcon name="delete" />
                            <span>{avatarDeleting(item) ? "删除中" : "删除"}</span>
                          </button>
                        </div>
                      </div>
                    </article>
                  )}</For>
                </div>
              </Show>
            </Show>
          </Show>
        </div>
      </section>
    </div>
  </Show>;
}
