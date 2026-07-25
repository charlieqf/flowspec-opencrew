# Koubo Model-Aware Video Settings Implementation Plan

- Date: 2026-06-23
- Module: OpenClip / Koubo Storyboard / Upload Asset Library / Videos + Videos-Agent
- Scope: Video Settings, video reference slots, model-aware frontend validation, backend validation alignment
- Target readers: frontend and backend engineers working on Koubo Asset Library video generation

## 1. Background

Upload Asset Library currently supports several video model aliases with different reference requirements:

- Max HR1.0: Chanjing HappyHorse multi-image reference generation.
- Max WR2.7: Wan video-reference generation.
- Max SI2: Seedance single-image generation.
- Max SR2: Seedance multimodal image/audio/video reference generation.

The backend already routes these aliases to different provider paths and performs some validation, but the right-side `Video Settings` panel is still generic. It exposes confirmation mode, aspect, duration, count, video model alias, and agent model without showing the selected model's reference requirements or preventing invalid combinations before submit.

The composer already has a generic reference pool:

- Lower-library image cards can add image references.
- Existing selected references render as chips through `referenceAssets()`.
- The composer has a disabled `+` button. It is currently a placeholder that points users to select assets from the lower library.
- The "Load consistency reference images" button is active. It opens a picker for Session consistency images such as host and product references, then adds the selected images to `localReferences`. These images are part of the same `reference_assets` / `reference_images` generation payload path.

The missing piece is model-aware slotting and validation. Today the UI does not distinguish whether a selected reference is acceptable for Max SI2, Max HR1.0, Max WR2.7, or Max SR2.

## 2. Goals

1. Make `Video Settings` model-aware.
2. Show only parameters that apply to the selected model.
3. Show required reference slots for the selected model.
4. Support adding references by click, drag-and-drop, local upload, and Session consistency references where appropriate.
5. Validate invalid combinations in the frontend before generation.
6. Keep backend validation as the final source of truth.
7. Preserve the existing generation API payload shape so the change is incremental.

Non-goals for the first pass:

- Reworking provider configuration UI in Model Config.
- Adding every provider-specific advanced option.
- Replacing backend provider modules.

## 3. Current State

### 3.1 Video Settings

Current persisted settings payload:

- `confirmBeforeGenerate`
- `aspect`
- `duration`
- `count`
- `referenceMode`
- `agentVideoAlias`
- `provider`
- `model`
- `chatProvider`
- `chatModel` for Videos-Agent only

Main frontend file:

- `frontend/src/modules/koubo/UploadAssetLibrary/components/VideoAgentPanel.jsx`

Current settings UI:

- Confirmation mode: Always / Never.
- Aspect: `9:16`, `16:9`.
- Duration presets: `4s`, `8s`, `15s`, plus custom number input.
- Count: `x1`, `x2`.
- Video model alias selector.
- Agent model selector for Videos-Agent.

Count caveat:

- Direct Videos currently has an endpoint-level loop that can run the same request more than once.
- Videos-Agent parsed `<VIDEO_GENERATION_REQUEST>` does not carry `count`; provider calls are single-output in the agent path.
- Provider-native batch count must not be implied by the UI. Gemini provider calls currently send `sampleCount: 1`.

Important backend schema constraint:

- `normalize_video_api_settings()` currently only persists `referenceMode` values `selected_images` and `none`.
- Advanced video fields are not persisted today. If advanced options are added later, the settings schema and normalizer must be extended or those fields should remain request-scoped only.

### 3.2 Reference Selection

The video composer reference source is not plain `selectedIds` alone. It is:

```js
referenceAssets() = mergeAssetsByPath(props.referenceAssets(), localReferences())
```

Observed behavior:

- Multiple images can be added from the lower library and shown as reference chips.
- Consistency references are added through `localReferences` with roles such as `HOST_REFERENCE` and `PRODUCT_REFERENCE`.
- Direct Videos consumes the merged references, splits them by media kind, and sends `reference_assets`, `reference_images`, `reference_audios`, and `reference_videos`.
- Videos-Agent sends the same split references in `client_context`, but the final generation request is still produced by the LLM as `<VIDEO_GENERATION_REQUEST>`.
- The video composer box does not currently have drop handlers.
- Videos in `VideoWorkspaceLibrary` are draggable but do not have an "Add as reference" action or selected state.
- Audio assets exist in overlay state but are not exposed as normal reference cards in the Videos/Videos-Agent workspace.

### 3.3 Videos-Agent Difference

Direct Videos and Videos-Agent cannot be treated as identical submit paths:

- Direct Videos sends the selected references directly to the backend generation endpoint.
- Videos-Agent sends selected references as context, then the LLM emits a `<VIDEO_GENERATION_REQUEST>`.
- The LLM can omit, reorder, or change references unless the backend validates or patches the parsed request.
- Direct Videos and Videos-Agent parsed generation both ultimately enter `generate_asset_library_video()`. Reuse that alias validation as the final guard; do not build a separate duplicate validator for agent requests.

Implementation must choose one agent policy:

- Strict policy: the generated request must match the user's current selected slots, otherwise backend rejects with a structured error.
- Assisted policy: backend may fill missing references from selected slots before generation, and records this in audit metadata.

Recommended first pass: strict policy for invalid media types and missing required references, assisted policy only for empty provider/model fallback from `VideosAgentSettings.json`.

## 4. Capability Source of Truth

Do not create a long-lived third capability source in the frontend.

The resolver should derive base capability from the Video Config response:

- `providers[].models[].duration`
- `providers[].models[].input_modes`
- `providers[].models[].reference_images`
- `providers[].models[].reference_audios`
- `providers[].models[].reference_videos`
- `providers[].models[].audio_input`

Then apply alias-specific product constraints:

- Alias normalization: `String(alias || "").replace(/\s+/g, "").toLowerCase()`.
- Match model metadata by `provider + model`, not by model string alone. This matters because `happyhorse-1.0-r2v` appears under more than one provider with different runtime semantics.
- Runtime normalization remains authoritative. For example Chanjing/HappyHorse duration is normalized to `5/6/10` by `video_provider_seconds("chanjing", ...)`.

Longer-term preferred shape:

- Backend returns an "effective video capability" for each alias from the same validation code that generation uses.
- Frontend only renders and validates against that effective capability.

## 5. Model Capability Matrix

| Alias | Provider / Model | Required References | Optional References | Disabled References | Reference Mode | Duration | Notes |
|---|---|---:|---:|---:|---|---|---|
| Max SI2 | OpenRouter / configured Seedance SI2 target | exactly 1 image | none | audio, video, extra images | `first_frame` | OpenRouter Seedance runtime clamp, currently 4-15s | Backend currently rejects more than one image but still must reject zero images. |
| Max HR1.0 | Chanjing / `happyhorse-1.0-r2v` | 1-3 images | none | audio, video | provider R2V image refs | `5/6/10` after Chanjing normalization | First image is start/target frame; extra images map to provider reference images. |
| Max WR2.7 | Wan / `wan2.7-r2v` | exactly 1 video | recommended decision: optional 1 image first frame | audio, extra videos, extra images if first-pass cap is 1 | provider R2V video refs | Wan runtime clamp, currently 3-30s | Catalog currently says up to 3 images; provider module currently uses only the first image. Align catalog, UI, and validation. |
| Max SR2 | OpenRouter / `bytedance/seedance-2.0` | none by type; at least one reference is recommended | images, audio, video | none within limits | `input_references` | OpenRouter Seedance runtime clamp, currently 4-15s | Catalog says image max 8, audio max 4, video max 4. Total cap must be derived from per-type caps, not hard-coded. |

Recommended product decisions for this implementation:

1. Max WR2.7 allows exactly 1 required video plus at most 1 optional image first frame.
2. Max SR2 supports 8 images, 4 audios, and 4 videos.
3. Max SR2 total reference count is `image.max + audio.max + video.max` unless backend/provider metadata declares a smaller total cap.

## 6. Proposed Frontend Design

### 6.1 Capability Resolver

Add a capability resolver next to `VideoAgentPanel` or in a new shared helper:

- `frontend/src/modules/koubo/UploadAssetLibrary/videoModelCapabilities.js`

Suggested API:

```js
export function resolveVideoModelCapability(settings, modelConfig) {
  return {
    aliasKey: "maxhr10",
    provider: "chanjing",
    model: "happyhorse-1.0-r2v",
    source: {
      modelConfigProvider: "chanjing",
      modelConfigModel: "happyhorse-1.0-r2v",
      aliasPatchApplied: true,
    },
    references: {
      images: { min: 1, max: 3, slots: ["start_frame", "reference_1", "reference_2"] },
      videos: { min: 0, max: 0, slots: [] },
      audios: { min: 0, max: 0, slots: [] },
      totalMax: 3,
    },
    params: {
      aspect: { allowed: ["9:16", "16:9", "1:1"], defaultValue: "9:16" },
      duration: { presets: [5, 6, 10], min: 5, max: 10 },
      count: { allowed: [1] },
    },
    referenceMode: "",
  };
}
```

Resolver rules:

- Find the selected alias from `modelConfig.agent_model_aliases`.
- Resolve its model metadata by `provider + model` inside `modelConfig.providers[].models[]`.
- Use model metadata for base reference and duration limits.
- Apply alias patches for Max SI2, Max HR1.0, Max WR2.7, and Max SR2.
- Use `capability.referenceMode` as the only authority for payload `reference_mode`.
- Remove or bypass the old heuristic that turns any video/audio reference into `input_references`; that is wrong for Max WR2.7.

### 6.2 Phase 1 Reference Adapter

Phase 1 validation must not wait for the new slot UI.

Add an adapter that converts the existing flat reference pool to the validator input:

```js
export function referencesToVideoSlots(items = []) {
  return {
    images: items.filter((item) => assetKind(item) === "image"),
    videos: items.filter((item) => assetKind(item) === "video"),
    audios: items.filter((item) => assetKind(item) === "audio"),
  };
}
```

This adapter should include:

- Lower-library selected image references.
- Locally loaded consistency references.
- Future video/audio references once the workspace exposes them.

### 6.3 Slot-Based Reference State

Phase 2 introduces dedicated slot state. Its ownership must be explicit.

Recommended ownership:

- Keep `UploadAssetLibraryOverlay` as the durable selected-reference owner.
- Let `VideoAgentPanel` render and edit slot views through callbacks.
- Keep `localReferences` only for transient composer additions such as consistency references until they are promoted to slot state.

Slot state shape:

```js
const videoReferenceSlots = {
  images: [],
  videos: [],
  audios: [],
};
```

This state is converted to the existing payload at submit time:

```js
{
  reference_assets,
  reference_images,
  reference_audios,
  reference_videos,
  reference_mode,
}
```

This preserves backend API compatibility.

### 6.4 Slot UI

Add a `Reference Slots` section above the composer textarea or inside `Video Settings` below model selection.

Each slot should support:

- Empty state with accepted asset type.
- Click-to-fill from selected library asset where possible.
- Drag asset card into the slot.
- Drop local file into the slot and upload it.
- Replace existing asset.
- Remove existing asset.
- Reorder images for multi-image models.
- Load Session consistency references into image slots when the selected model accepts images.

Slot generation:

- Max SI2: one required image slot.
- Max HR1.0: three image slots, first required.
- Max WR2.7: one required video slot plus one optional image first-frame slot.
- Max SR2: flexible image/audio/video groups with counters.

### 6.5 Drag-And-Drop

Wire drag-and-drop in `VideoAgentPanel`:

- Add `onDragEnter`, `onDragOver`, `onDragLeave`, and `onDrop` to the video composer box and each slot.
- Accept `application/x-koubo-storyboard-asset` from existing asset cards.
- For local files, route by MIME / extension:
  - image -> image slot
  - audio -> audio slot
  - video -> video slot
- Reject unsupported drops with a visible error message.
- Reject over-limit drops before mutating state.

Update source cards:

- `ImageCard` already sets `application/x-koubo-storyboard-asset`.
- `VideoCard` already sets `application/x-koubo-storyboard-asset`, but it must also expose an "Add as reference" action and selected state in the video workspace.
- Audio cards should be exposed only in the SR2 slot picker for the first pass, not as a general Videos workspace section.

### 6.6 Model-Aware Settings Controls

The settings panel should render controls from capability:

- Aspect:
  - Show only allowed aspects.
  - Include `1:1` only where backend accepts it for the selected route.
- Duration:
  - Show model presets from effective capability.
  - Clamp or reject invalid custom values before submit.
  - For Chanjing HappyHorse, show `5/6/10s` because runtime rounds to those values.
- Count:
  - Direct Videos may keep `x1/x2` as "run the same request N times" if product wants batch variations.
  - Videos-Agent should hide or disable count until `<VIDEO_GENERATION_REQUEST>` supports it.
  - Provider single-call count must not be implied; Gemini currently sends `sampleCount: 1`.
- Advanced options:
  - Add collapsed advanced section after MVP.
  - Pass advanced options through the generation payload only when the selected model supports them.
  - Prefer request-scoped advanced options. Do not persist globally unless settings schema is extended with provider/model scoping.

## 7. Frontend Validation

Add a submit-time validator:

```js
export function validateVideoGenerationInputs(capability, slots) {
  return {
    ok: false,
    errors: [
      "Max HR1.0 requires 1 to 3 image references.",
    ],
  };
}
```

Phase 1 input:

- `slots = referencesToVideoSlots(referenceAssets())`

Phase 2 input:

- `slots = videoReferenceSlots`

Validation rules:

- Max SI2:
  - image count must be exactly 1.
  - audio count must be 0.
  - video count must be 0.
- Max HR1.0:
  - image count must be 1-3.
  - audio count must be 0.
  - video count must be 0.
- Max WR2.7:
  - video count must be exactly 1.
  - audio count must be 0.
  - image count must be 0-1 for the recommended first-pass policy.
- Max SR2:
  - image count max 8.
  - audio count max 4.
  - video count max 4.
  - total reference count max is derived from capability, not hard-coded.

The UI should show errors near the slots and prevent generation until valid.

Videos-Agent must also validate after the LLM emits `<VIDEO_GENERATION_REQUEST>`:

- Parsed request must not exceed selected model capability.
- Required references cannot be omitted.
- Invalid media types must fail before provider calls.
- Failure detail must include the selected alias, expected counts, and actual counts.

## 8. Backend Alignment

Main backend files:

- `backend/opcrew_backend/koubo/koubo_storyboard/asset_video_generation_services.py`
- `backend/opcrew_backend/koubo/koubo_storyboard/agent_chat_routes.py`
- `backend/opcrew_backend/koubo/koubo_storyboard/asset_routes.py`
- `ModelConfig/backend/opcrew_model_config/media_model_config.py`

Required backend changes:

1. Max SI2 must require exactly one image.
   - Current alias rule rejects more than one image but allows zero images.
2. Max SR2 image limit must align to 8.
   - Current catalog says max 8 images.
   - Current `validate_video_reference_images` returns `refs[:4]`.
   - Videos-Agent `normalize_reference_path_list` also defaults to 4 per list.
   - Implementation can safely widen shared image parsing to 8 only because alias validation still constrains Max SI2 to exactly 1 image, Max HR1.0 to 1-3 images, and Max WR2.7 to at most 1 image.
3. Max SR2 total cap must be derived.
   - If image/audio/video caps are 8/4/4, total max is 16 unless provider metadata declares a smaller total.
4. Max WR2.7 image policy must be enforced.
   - Recommended first pass: require exactly one video, allow at most one optional image first frame, reject audio.
   - Update catalog or alias effective capability if keeping the UI cap at 1.
   - Provider module currently uses only the first image, so accepting more than one image would be misleading.
5. `reference_mode` must come from effective capability.
   - Max SR2 -> `input_references`.
   - Max SI2 -> `first_frame`.
   - Max WR2.7 -> provider R2V video-reference mode, not frontend's generic `input_references` heuristic.
6. Keep backend rejection messages structured enough for frontend display:

```json
{
  "message": "Max HR1.0 requires 1 to 3 image references.",
  "agent_video_alias": "Max HR1.0",
  "reference_image_count": 4,
  "reference_audio_count": 0,
  "reference_video_count": 0,
  "limits": {
    "images": {"min": 1, "max": 3},
    "audios": {"min": 0, "max": 0},
    "videos": {"min": 0, "max": 0}
  }
}
```

Settings schema changes:

- If advanced options are persisted, bump `VIDEO_API_SETTINGS_SCHEMA` and `VIDEOS_AGENT_SETTINGS_SCHEMA`.
- Extend `normalize_video_api_settings()` and `normalize_videos_agent_settings()`.
- Prefer request-scoped advanced options until provider/model scoping is defined.

## 9. Payload Contract

The frontend should continue sending the current Direct Videos payload shape:

```json
{
  "title": "Direct video generation",
  "prompt": "...",
  "aspect": "9:16",
  "duration": 6,
  "count": 1,
  "provider": "chanjing",
  "model": "happyhorse-1.0-r2v",
  "agentVideoAlias": "Max HR1.0",
  "referenceMode": "",
  "reference_assets": [],
  "reference_images": [],
  "reference_audios": [],
  "reference_videos": []
}
```

For Videos-Agent, selected references should continue to be included in `client_context`:

```json
{
  "selected_reference_assets": [],
  "selected_reference_images": [],
  "selected_reference_audios": [],
  "selected_reference_videos": [],
  "video_generation_settings": {
    "agentVideoAlias": "Max SR2",
    "referenceMode": "input_references"
  }
}
```

Agent generation remains a separate parsed request:

```json
{
  "title": "Agent generated video",
  "prompt": "...",
  "duration": 4,
  "aspect": "9:16",
  "reference_images": [],
  "reference_audios": [],
  "reference_videos": [],
  "reference_mode": "input_references",
  "provider": "",
  "model": ""
}
```

Backend must validate the parsed agent request against the selected alias capability before provider calls.

## 10. Implementation Steps

### Phase 1: Effective Capability + Flat-Reference Validation

1. Add `videoModelCapabilities.js`.
2. Resolve base capability from Video Config by `provider + model`.
3. Add alias patches for Max SI2, Max HR1.0, Max WR2.7, and Max SR2.
4. Add `referencesToVideoSlots(referenceAssets())`.
5. Add frontend validation before Direct Videos generation and Videos-Agent send.
6. Show a compact model requirement summary in `Video Settings`.
7. Replace `referenceModeForVideoSettings()` heuristics with `capability.referenceMode`.
8. Backend fixes:
   - SI2 exactly one image.
   - SR2 image count 8 in direct and agent paths.
   - SR2 total cap derived from per-type caps.
   - WR2.7 exactly one video, optional one image first frame.
   - Structured validation errors.

### Phase 2: Slot UI

1. Add `VideoReferenceSlots.jsx`.
2. Render slots in `VideoAgentPanel`.
3. Use overlay-owned selected-reference state as the durable source.
4. Convert slots to current payload shape.
5. Preserve existing reference chip display during migration.
6. Add remove, replace, and image reorder actions.
7. Route consistency references into image slots when the selected model accepts images.

### Phase 3: Drag-And-Drop + Audio/Video Reference Entry

1. Add drop handlers to slots and composer.
2. Add "Add as reference" action and selected state to video cards.
3. Expose audio references only inside the SR2 slot picker.
4. Add local file drop/upload into the correct slot type.
5. Add visual invalid-drop feedback.

### Phase 4: Advanced Parameters

1. Add collapsed advanced options.
2. Start with provider-backed options only:
   - HappyHorse `quality_mode`.
   - HappyHorse `clarity`.
   - OpenRouter `resolution`.
   - SR2 `generate_audio`.
3. Pass advanced options through generation payload only when the selected model supports them.
4. Persist advanced options only after settings schema supports provider/model scoped values.

## 11. Test Plan

### Frontend Contract Tests

- Resolver matches model metadata by `provider + model`, not model string alone.
- Max HR1.0 resolved through Chanjing shows `5/6/10s`.
- Selecting Max SI2 shows one required image slot and hides audio/video slots.
- Selecting Max HR1.0 shows 1-3 image slots and rejects audio/video references.
- Selecting Max WR2.7 shows one required video slot and one optional image slot.
- Selecting Max SR2 shows image/audio/video groups and max counters 8/4/4.
- SR2 total max is computed from capability, not hard-coded to 12.
- The composer `+` button uploads reference files and applies the selected model's slot limits.
- Loading consistency references adds image references and is accepted/rejected by the selected model capability.
- Dragging a second image into SI2 is rejected.
- Dragging a fourth image into HR1.0 is rejected.
- Dragging a second video or audio into WR2.7 is rejected.
- Dragging audio/video into SR2 is accepted within limits.
- Videos-Agent hides or disables count until agent generation requests support count.

### Backend Contract Tests

- Max SI2 with zero images fails.
- Max SI2 with one image succeeds through first-frame mode.
- Max HR1.0 with one to three images reaches Chanjing HappyHorse R2V path.
- Max HR1.0 with four images fails.
- Max WR2.7 without video fails.
- Max WR2.7 with one video reaches Wan R2V path.
- Max WR2.7 with two videos fails.
- Max WR2.7 with two images fails if first-pass cap is 1.
- Max SR2 accepts up to 8 images through direct Videos.
- Max SR2 accepts up to 8 images through Videos-Agent parsed request.
- Max SR2 sends `input_references` for image/audio/video and sets `generate_audio`.
- Structured backend validation errors include actual counts and limits.

### Manual QA

1. Open Asset Library -> Videos.
2. Select each model alias in Video Settings.
3. Verify capability summary and slot UI change immediately.
4. Add references by clicking existing image assets.
5. Add consistency references and verify they appear as image references.
6. Add video references for Max WR2.7.
7. Add audio/video references for Max SR2.
8. Add references by dragging existing assets.
9. Add references by dropping local files.
10. Try invalid combinations and verify generation is blocked before network calls.
11. Generate one valid video for each alias in a test session.
12. Repeat in Videos-Agent and verify the parsed `<VIDEO_GENERATION_REQUEST>` is validated against the selected slots/capability.
13. Before enabling `1:1`, verify each selected provider route actually accepts 1:1 at provider runtime; backend `video_size_for_aspect()` mapping alone is not sufficient evidence.

## 12. Acceptance Criteria

- Users can see exactly what each selected video model requires before generation.
- Users can add multiple image references for Max HR1.0 and Max SR2.
- Users can add video references for Max WR2.7 and Max SR2.
- Users can add audio references for Max SR2.
- Users can load Session host/product consistency references where the selected model accepts images.
- Invalid reference combinations are blocked in the UI with clear messages.
- Videos-Agent parsed generation requests are validated before provider calls.
- Backend validation matches frontend validation.
- Existing direct Videos and Videos-Agent generation flows still use the same backend generation service.
- Existing payload fields remain backward compatible.

## 13. Product Decisions for This Version

1. Max WR2.7 image policy:
   - Use exactly one required video plus at most one optional image first frame.
   - Align UI, backend validation, and effective capability to max 1 image.
   - Update catalog metadata or alias patch so users do not see max 3 unless provider support is implemented and tested.
2. Max SR2 image max:
   - Support 8 images.
   - Update direct backend validation and Videos-Agent parsing from 4 to 8 for image references.
   - Derive total reference max from capability.
3. Audio reference entry:
   - Expose audio reference cards only inside the SR2 slot picker for the first pass.
4. Advanced provider options:
   - Attach advanced options to generation requests.
   - Do not persist globally until provider/model scoped settings are supported.
