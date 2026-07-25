# Koubo Upload Asset Library Video / Videos-Agent Design

- Date: 2026-06-14
- Module: OpenClip / Koubo Storyboard / Upload Asset Library
- Scope: Videos workspace, Videos-Agent workspace, Video Config, Session Context settings

## 1. Product Boundary

Videos and Videos-Agent are intentionally different products.

### Videos

Videos is a direct video API workspace. It should behave like Images:

- No OpenCode Agent session is created for normal video generation.
- No multi-turn chat context is required or stored.
- The user selects Images from the lower library as references.
- The user writes one prompt and sends one generation request.
- The request uses the currently selected Video Settings: aspect, duration, count, provider, model.
- The backend calls the configured video provider API directly and saves generated MP4 assets to the Videos library.

### Videos-Agent

Videos-Agent is the OpenCode-backed video assistant:

- It uses an `asset_video` Agent chat session.
- It has multi-turn context and remembers the Agent conversation through OpenCode.
- It receives curated asset/storyboard/client context.
- It can analyze the video library, suggest organization or bindings, and produce structured advice.
- When the user explicitly asks to generate video, it emits one `<VIDEO_GENERATION_REQUEST>` block. The backend listens to that block and then calls the same provider generation service.

## 2. Configuration

Video model aliases come from Video Config.

Session Context stores the UI settings separately:

- `SessionContext/VideoAPISettings.json`: direct Videos API settings.
- `SessionContext/VideosAgentSettings.json`: Videos-Agent settings.

Both settings support:

- `confirmBeforeGenerate`
- `aspect`: `9:16` or `16:9`
- `duration`: preset or custom seconds
- `count`: `1` or `2`
- `referenceMode`
- `agentVideoAlias`
- `provider`
- `model`

Videos must pass `provider` and `model` directly to the direct video API. It must not rely on the Agent to echo those fields.

Videos-Agent may include the selected settings in `client_context.video_generation_settings`, but final provider selection still depends on the generated `<VIDEO_GENERATION_REQUEST>` payload unless backend fallback logic explicitly reads `VideosAgentSettings.json`.

## 3. Direct Videos Flow

1. User opens Videos.
2. User selects Images in the lower library.
3. User may load Host/Product consistency references from the current Session.
4. User opens Video Settings and chooses model alias, aspect, duration, count, and confirmation mode.
5. When the user saves Video Settings, the backend copies the provider-specific video template from `ToolLibrary/Analysis_V1/Reference/05_02/` into `SessionContext/PromptBuilder/` as a Session-local snapshot.
6. User may open Prompt Builder. The backend reads the current video template snapshot and only creates it if missing; opening Builder must not overwrite user edits in the snapshot.
7. User applies the Builder prompt or writes a prompt directly.
8. Frontend calls:
   - `POST /api/koubo-storyboard/tasks/{task_id}/asset-library/video-api/generate/events`
9. Backend validates references, provider, model, aspect, duration, and count.
10. Backend builds an effective video prompt by combining the user/builder prompt with role-bound reference guidance.
11. Backend calls `generate_asset_library_video` once per requested count.
12. Provider-specific code calls the configured supported video API, currently Gemini, Wan, OpenAI, or xAI.
13. MP4 files are downloaded into `SessionOutput/storyboard/assets/videos/`.
14. Assets are added to the asset manifest and streamed back to the frontend.

This path has no OpenCode `ensure-session`, `message`, `events`, or `abort` dependency.

## 3.1 Video Prompt Builder

Videos uses the same template source pattern as Images, but video Prompt Builder must save into the copied video template snapshot itself. It must not create `VideoPrompt.json`, `Draft_*_VideoPrompt.json`, or `Applied_*_VideoPrompt.json` files, because those names overlap with normal video generation prompt sidecars.

- Template source directory: `ToolLibrary/Analysis_V1/Reference/05_02/`.
- The template is selected from the current Video Config alias provider/model.
- Supported template snapshots:
  - xAI/Grok: `Video_Grok.md` -> `SessionContext/PromptBuilder/Ref_05_02_Video_Grok.md`
  - Gemini: `Video_Gemini.md` -> `SessionContext/PromptBuilder/Ref_05_02_Video_Gemini.md`
  - OpenAI/GPT/Sora-style video: `Video_GPT.md` -> `SessionContext/PromptBuilder/Ref_05_02_Video_GPT.md`
  - Wan/DashScope: `Video_Wan.md` -> `SessionContext/PromptBuilder/Ref_05_02_Video_Wan.md`
  - OpenRouter: `Video_OpenRouter.md` -> `SessionContext/PromptBuilder/Ref_05_02_Video_OpenRouter.md`
  - Seedance/ByteDance: `Video_Seedance.md` -> `SessionContext/PromptBuilder/Ref_05_02_Video_Seedance.md`
- Editable prompt state: the selected snapshot itself, for example `SessionContext/PromptBuilder/Ref_05_02_Video_Grok.md`.
- Opening Prompt Builder reads the selected snapshot and renders `positive_prompt`, `negative_prompt`, and full prompt from its `OPENCREW:*` blocks.
- Applying or saving Prompt Builder edits writes override blocks back into the same snapshot:
  - `VIDEO_PROMPT_BUILDER_POSITIVE_OVERRIDE`
  - `VIDEO_PROMPT_BUILDER_NEGATIVE_OVERRIDE`
  - `VIDEO_PROMPT_BUILDER_PROMPT_OVERRIDE`
- The generation request must include `prompt_builder_request_id` and `prompt_builder_applied_path` when the user generated the prompt through Builder.
- The generated video sidecar must keep both the original `prompt` and the final `effective_prompt`.

The template copy happens when the user saves Video Settings after selecting a Video Config alias. This is the moment the provider-specific reference is copied from `ToolLibrary/Analysis_V1/Reference/05_02/` into `SessionContext/PromptBuilder/`. Opening Prompt Builder may create the selected snapshot if it is missing, but it must not overwrite an existing snapshot; user edits live in that copied Ref md file.

## 3.2 Host/Product Reference Handling

Videos must support the same role-bound reference semantics as Images Agent:

- Selected lower-library Images remain valid references.
- The composer must also allow loading Session consistency references:
  - Host: `SessionContext/Consistency/HOST.png` or the active Host Builder output.
  - Product: `SessionContext/Consistency/Product.png` or the active Product Builder output.
- References must preserve roles:
  - `TARGET_FRAME`
  - `HOST_REFERENCE`
  - `PRODUCT_REFERENCE`
  - `REFERENCE_IMAGE`
- Prompt Builder must include the ordered reference list and role summary when creating the video prompt.
- Video generation must prepend reference guidance to the effective provider prompt:
  - `HOST_REFERENCE` controls visible presenter identity, styling, face, hair, clothing, microphone/accessories, and human continuity.
  - `PRODUCT_REFERENCE` controls package identity, structure, color hierarchy, label layout, material, and geometry.
  - `TARGET_FRAME` controls editable base scene, composition, camera angle, background, pose category, hand/product position, lighting, shadows, and texture.
  - User/spoken words are semantic guidance only and must not become subtitles, labels, QR codes, watermarks, captions, or UI text.

## 4. Videos-Agent Flow

1. User opens Videos-Agent.
2. Frontend ensures an `asset_video` OpenCode session.
3. Frontend sends the user message plus client context.
4. OpenCode Agent replies with analysis, advice, or one `<VIDEO_GENERATION_REQUEST>` block.
5. Backend stream listener extracts that block after the assistant message completes.
6. Backend calls `generate_asset_library_video`.
7. Generated videos are saved to the same Videos asset library.
8. Completion events are streamed back into the Agent conversation UI.

## 5. Acceptance Criteria

- Videos generation must work without creating or requiring an OpenCode Agent session.
- Videos composer must not show Run model choices.
- Videos settings must save to `VideoAPISettings.json`.
- Videos Prompt Builder must copy the selected video template into `SessionContext/PromptBuilder` when settings are saved.
- Videos Prompt Builder must display and save the selected Ref md snapshot, for example `SessionContext/PromptBuilder/Ref_05_02_Video_Grok.md`; it must not use `VideoPrompt.json` for Builder state.
- Videos generation sidecars must record `prompt`, `effective_prompt`, reference roles, and Prompt Builder audit paths when available.
- Videos composer must support loading Host/Product consistency references.
- Videos-Agent must keep Run model choices and OpenCode chat behavior.
- Videos-Agent settings must save to `VideosAgentSettings.json`.
- Both surfaces must use the same Video Config aliases for provider/model choices.
- Generated videos from either surface must land in the same Videos asset library.

## 6. Implementation Pointers

- Frontend direct API call: `kbApi.streamAssetLibraryVideoGenerate`.
- Frontend direct workspace: `VideoAgentPanel` with `variant="workspace"`.
- Frontend Agent workspace: `VideoAgentPanel` with `variant="agent"`.
- Backend direct route: `/asset-library/video-api/generate/events`.
- Backend Agent route: `/agents/asset_video/chat/*`.
- Shared generation service: `generate_asset_library_video`.

## 7. Test Plan

### 7.1 Provider API contract

- Gemini must match `ToolLibrary/Analysis_V1/video_plan_executor_modules/video_gemini.py` (`Video_Gemini.py` in product shorthand):
  - direct `urllib.request.urlopen`;
  - `models/{model}:predictLongRunning?key=...`;
  - first reference image as `image.mimeType` and `image.bytesBase64Encoded`;
  - poll `v1beta/{operation_name}`;
  - download with `x-goog-api-key`;
  - no OpenCode Agent dependency.
- Grok/xAI must match `ToolLibrary/Analysis_V1/video_plan_executor_modules/video_grok.py` (`Video_Grok.py` in product shorthand):
  - first try normal `urllib.request.urlopen`;
  - only for proxy tunnel failures, fall back to `ProxyHandler({})`;
  - endpoint `https://api.x.ai/v1/videos/generations`;
  - payload shape: `model`, `prompt`, `duration`, `aspect_ratio`, `resolution`, and optional first-frame `image.url`;
  - poll `https://api.x.ai/v1/videos/{request_id}`;
  - download with `Authorization: Bearer ...`.
- Contract tests must prevent Gemini and Grok call-shapes from leaking into each other.

### 7.2 Direct Videos workspace

- Open Videos without creating an OpenCode session.
- Select the first image in the lower Images library.
- Choose a Video Config alias in Video Settings.
- Save settings and verify the provider-specific template snapshot is copied to `SessionContext/PromptBuilder/Ref_05_02_Video_*.md`.
- Open Prompt Builder and verify it reads the selected video template, not image templates and not `VideoPrompt.json`.
- Generate one 4-second video.
- Verify:
  - event stream shows started/completed;
  - generated MP4 is saved under `SessionOutput/storyboard/assets/videos/`;
  - Videos count increases;
  - sidecar JSON contains `prompt`, `effective_prompt`, reference roles, provider, model, aspect, duration, and Prompt Builder audit path when applicable.

### 7.3 Videos-Agent workspace

- Open Videos-Agent and ensure one `asset_video` OpenCode session.
- Send a normal planning question and verify multi-turn context remains in OpenCode.
- Send a generation request and verify the assistant emits one completed `<VIDEO_GENERATION_REQUEST>` block.
- Verify backend extracts the completed block only after assistant message completion.
- Verify generated assets are saved to the same Videos library and streamed back into the Agent conversation.

### 7.4 Host/Product references

- Load Host and Product consistency references from the composer.
- Verify selected references preserve roles:
  - `TARGET_FRAME`
  - `HOST_REFERENCE`
  - `PRODUCT_REFERENCE`
  - `REFERENCE_IMAGE`
- Verify the effective prompt prepends role-bound reference guidance.
- Verify user dialogue or semantic prompt text does not become subtitles, labels, QR codes, watermarks, captions, or UI text.

### 7.5 Standard Agent conversation presentation

- Video Agent messages must follow the same presentation rules as Images Agent:
  - user messages render as compact right-side bubbles;
  - assistant messages render as clean assistant bubbles;
  - reference lists render as small reference strips;
  - thinking/planning content is hidden behind a `Show thinking` disclosure;
  - internal protocol blocks, including `<VIDEO_GENERATION_REQUEST>`, are hidden from the main message and available only under `Details`;
  - generated video results render as a result card with playable video, saved filename, and actions.
- Main conversation must not show raw OpenCode protocol text such as:
  - `Produce exactly one ... JSON block`;
  - `The user explicitly requested ...`;
  - raw role/path object instructions;
  - raw `<VIDEO_GENERATION_REQUEST>` JSON.

### 7.6 UI verification

- Use Computer Use on `127.0.0.1:18080/#/koubo-asset-library/tasks/3`.
- Verify Videos and Images are still split into upper/lower library sections.
- Verify the direct Videos workspace composer can generate and display a saved video result card.
- Verify Videos-Agent conversation displays clean bubbles and hides internal request JSON under `Details`.
- Verify the right-side composer remains fixed and usable during long conversations.
