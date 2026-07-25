from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_scene_tts_uses_locked_output_even_when_cache_read_is_disabled() -> None:
    source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/tts_routes.py").read_text()

    assert 'output_rel = deps.text(payload.get("locked_output")) if len(prompts) == 1 and deps.text(payload.get("locked_output"))' in source
    assert 'payload.get("use_locked_cache") and len(prompts) == 1 and deps.text(payload.get("locked_output"))' not in source


def test_tts_agent_multi_line_generation_combines_segments_into_locked_output() -> None:
    route_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/tts_routes.py").read_text()
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()

    assert 'combine_asset_audio = bool(payload.get("asset_library_only") and len(prompts) > 1 and deps.text(payload.get("locked_output")))' in route_source
    assert "concat_asset_audio_segments(segment_results)" in route_source
    assert 'ordered_segments = sorted(segment_results, key=lambda item: int(item.get("segment_index") or 0))' in route_source
    assert "normalized_paths: list[Path] = []" in route_source
    assert "aresample=48000,aformat=channel_layouts=stereo,asetpts=N/SR/TB" in route_source
    assert "concat_file_line(path) for path in normalized_paths" in route_source
    assert '"normalized_segment_count": len(normalized_paths)' in route_source
    assert "if len(segment_results) != len(prompts):" in route_source
    assert "combined Asset Audio was not created" in route_source
    assert '"segment_count_expected": len(prompts)' in route_source
    assert "'type': 'segment_completed'" in route_source
    assert 'await generateTtsAudio(dialogues())' in model_source
    assert 'output: sessionOutput || `${dir}/${name}.${ext}`' in model_source
    assert 'const dir = options.previewOnly ? "SessionOutput/storyboard/Working" : ASSET_AUDIO_REL' in model_source


def test_scene_tts_writes_manifest_even_when_cache_read_is_disabled() -> None:
    source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/tts_workflow_services.py").read_text()

    assert 'def read_locked_tts_cache' in source
    assert 'if not payload.get("use_locked_cache") or not sc.text(payload.get("locked_output"))' in source
    assert 'def write_locked_tts_manifest' in source
    assert 'if not sc.text(payload.get("locked_manifest")) or not sc.text(payload.get("locked_config_key"))' in source


def test_tts_agent_validates_role_voice_against_current_provider() -> None:
    source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()

    assert "function voiceForSettings(settings, requested, index = 0)" in source
    assert "providerVoices.includes(requestedId)" in source
    assert "const voiceId = voiceForSettings(settings, dialogue.voice_id || role.voice, roleIndex)" in source


def test_tts_agent_voice_picker_shows_metadata_and_supports_preview() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    workspace_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentWorkspace.jsx").read_text()
    css_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgent.css").read_text()

    assert "function voiceOptionFromValue(value, index = 0)" in model_source
    assert "function voiceOptionItems(settings)" in model_source
    assert "async function previewVoice(voiceId, context = {})" in model_source
    assert "voiceOptions: () => voiceOptionItems(ttsSettings())" in model_source
    assert "function VoicePicker(props)" in workspace_source
    assert "voice.gender || \"未标注\"" in workspace_source
    assert "voice.description || voice.label || voice.voice_id" in workspace_source
    assert "controller.previewVoice(voice, { lineId: item().line_id })" in workspace_source
    assert 'placement="up"' in workspace_source
    assert ".ual-tts-voice-menu" in css_source
    assert ".ual-tts-voice-picker.is-up .ual-tts-voice-menu" in css_source
    assert ".ual-tts-voice-preview" in css_source


def test_tts_agent_switching_dialogue_speaker_refreshes_voice_and_prompt() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    workspace_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentWorkspace.jsx").read_text()

    assert "function updateDialogueSpeaker(lineId, speakerIdValue)" in model_source
    assert "voice_id: nextRole?.voice || item.voice_id" in model_source
    assert "final_prompt: buildPromptText(nextDialogue, nextRole || roleForDialogue(currentRoles, nextDialogue), nextDialogue.scenario_keywords || [])" in model_source
    assert "controller.updateDialogueSpeaker(item().line_id, event.currentTarget.value)" in workspace_source


def test_tts_agent_role_voice_changes_sync_matching_tts_rows() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    workspace_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentWorkspace.jsx").read_text()

    assert "function syncRoleVoiceToDialogues(nextRole)" in model_source
    assert "if (dialogue.voice_locked) return dialogue;" in model_source
    assert "return { ...dialogue, voice_id: nextVoice };" in model_source
    assert "nextPatch.voice_locked = text(nextPatch.voice_id) !== text(role.voice);" in model_source
    assert "voice_locked: text(current.voice) !== text(role.voice)" in model_source
    assert "voice_locked: false" in model_source
    assert "if (patch.voice !== undefined) syncRoleVoiceToDialogues(nextRoleForSync);" in model_source
    assert "syncRoleVoiceToDialogues(nextRoleForSync);" in model_source
    assert "controller.updateRole(role().speaker_id, { voice })" in workspace_source


def test_tts_agent_role_prefix_and_tts_tempo_are_first_class_fields() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    workspace_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentWorkspace.jsx").read_text()
    css_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgent.css").read_text()

    assert "function rolePromptPrefix(role = {})" in model_source
    assert "prompt_prefix: DEFAULT_ROLE_PREFIX" in model_source
    assert "prompt_prefix: nextPrefix" in model_source
    assert "prompt_prefix: patch.prompt_prefix !== undefined ? text(patch.prompt_prefix) : role.prompt_prefix" in model_source
    assert "tempo: clampTempo(dialogue.tempo || 1)" in model_source
    assert "tempo: clampTempo(dialogue.tempo || 1)," in model_source
    assert "Tempo" in workspace_source
    assert "Prompt Prefix" in workspace_source
    assert "ual-tts-tempo-input" in workspace_source
    assert 'title="角色配置" aria-label="角色配置"' in workspace_source
    assert "controller.openRoleGuide(role().speaker_id)}><FlowIcon name=\"tune\" />" in workspace_source
    assert ">角色配置</button>" not in workspace_source
    assert "controller.updateDialogue(item().line_id, { tempo: event.currentTarget.value })" in workspace_source
    assert "Style</span>" not in workspace_source
    assert "Pace</span>" not in workspace_source
    assert "--ual-tts-speaker-col: 90px;" in css_source
    assert "--ual-tts-voice-col: 100px;" in css_source
    assert "grid-template-columns: var(--ual-tts-speaker-col) var(--ual-tts-voice-col) minmax(260px, 1fr) auto;" in css_source
    assert "grid-template-columns: var(--ual-tts-speaker-col) var(--ual-tts-voice-col) 74px minmax(320px, 1fr) 118px;" in css_source


def test_tts_agent_manual_prompt_text_is_generation_source_of_truth() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()

    assert "function dialogueOutputText(dialogue, role)" in model_source
    assert "return source;" in model_source
    assert "text: dialogueOutputText(dialogue, role)" in model_source
    assert "return `${role.speaker}: ${dialogueOutputText(dialogue, role)}`;" in model_source
    assert model_source.count("sourceTextFromPromptPrompt(renderedPrompt(dialogue, role), dialogue.source_text)") == 1


def test_tts_agent_bytedance_maps_prompt_controls_to_structured_params() -> None:
    provider_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/media_tts_provider_services.py").read_text()
    model_config_source = (ROOT / "ModelConfig/backend/opcrew_model_config/media_model_config.py").read_text()

    for token in (
        "def tts_prompt_control_values(prompt: str, text_value: str) -> dict[str, Any]:",
        "def strip_tts_control_tags(value: str) -> str:",
        '"shouting": ("excited", 5)',
        '"very fast"',
        "control_values = tts_prompt_control_values(prompt, text_value)",
        'bytedance_extra["enable_emotion"] = True',
        'bytedance_extra["emotion"] = control_values["emotion"]',
        'bytedance_extra["speed_ratio"] = control_values["speed_ratio"]',
        "bytedance_tts_audio_bytes(api_key, model, voice_id, bytedance_text, bytedance_extra)",
    ):
        assert token in provider_source
    for token in (
        'audio["enable_emotion"] = bool(extra_payload.get("enable_emotion", True))',
        'audio["emotion"] = emotion',
        'audio["emotion_scale"] = int(extra_payload.get("emotion_scale") or 4)',
        'audio_params["enable_emotion"] = bool(extra_payload.get("enable_emotion", True))',
        'audio_params["emotion"] = emotion',
        'audio_params["emotion_scale"] = int(extra_payload.get("emotion_scale") or 4)',
    ):
        assert token in model_config_source


def test_tts_agent_audio_detail_uses_control_only_playback() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    workspace_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentWorkspace.jsx").read_text()
    css_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgent.css").read_text()

    assert "if (audioPlaying())" in model_source
    assert "stopAudio();" in model_source
    assert 'title={controller.audioPlaying() ? "停止播放" : controller.playAudioDisabledReason() || "播放声音"}' in workspace_source
    assert 'FlowIcon name={controller.audioPlaying() ? "radioButtonUnchecked" : "arrowForward"}' in workspace_source
    assert "ual-tts-audio-control-only" in workspace_source
    assert "controller.audioAsset().filename" not in workspace_source
    assert "controller.audioAsset().path" not in workspace_source
    assert ".ual-tts-audio-control-only" in css_source


def test_tts_agent_chat_matches_video_bubble_style_and_hides_output_paths() -> None:
    panel_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentPanel.jsx").read_text()
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    css_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgent.css").read_text()

    assert "function displayMessageText" in panel_source
    assert 'return "声音文件已生成到 Asset Audio，可以直接使用。";' in panel_source
    assert "replace(/输出[:：]\\s*SessionOutput\\/" in panel_source
    assert "ual-user-bubble" in panel_source
    assert "ual-assistant-bubble" in panel_source
    assert "ual-tts-message-label" in panel_source
    assert 'pushMessage("assistant", "声音文件已生成到 Asset Audio，可以直接使用。")' in model_source
    assert "输出：${completed.path}" not in model_source
    assert ".ual-tts-message .ual-user-bubble" in css_source
    assert ".ual-tts-message.is-status" in css_source


def test_tts_agent_role_inputs_keep_unique_ids_and_stable_focus() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    workspace_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentWorkspace.jsx").read_text()

    assert r"replace(/[^\p{L}\p{N}_:-]+/gu" in model_source
    assert "function uniqueRoleRows(rows)" in model_source
    assert "return uniqueRoleRows(inferRoleNames(value).map((name, index) => roleRowFromName(name, settings, index)))" in model_source
    assert "const nextSpeakerId = role.speaker_id || speakerId(speaker, index)" in model_source
    assert "import { For, Index, Show, createSignal, onCleanup } from \"solid-js\";" in workspace_source
    assert "<Index each={controller.roles()}" in workspace_source
    assert "<Index each={controller.promptItems()}" in workspace_source


def test_tts_agent_preserves_requested_single_role_count() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    route_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/tts_routes.py").read_text()
    overlay_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx").read_text()

    for token in (
        "function requestedRoleCount(value)",
        "function repeatedSingleRoleMentionCount(source)",
        "function impliedRoleCount(value)",
        "function enforceRoleCountForRequest(rows, request, settings)",
        "normalizeRoleDrafts(draft, settings, nextText)",
        "generatedDialogueRowsFromDraft(draft, nextRoles, nextText)",
        'return /对话|互相|交流/.test(text(value)) ? 2 : 1;',
    ):
        assert token in model_source

    assert 'if (names.length === 1) add("旁白")' not in model_source
    assert 'const DEFAULT_REQUEST = "生成一段可编辑的语音台词，我来调整";' in model_source

    for token in (
        "def repeated_single_role_mention_count(user_text: str) -> int:",
        "def requested_role_count(user_text: str) -> int:",
        "def implied_role_count(user_text: str) -> int:",
        "if requested_count and len(roles) > requested_count:",
        "Strictly preserve the requested role count",
        "用户说一个/一位/单人角色时，只返回 1 个 role",
        "不要为了对话感自动添加旁白、搭档或第二角色",
    ):
        assert token in route_source

    assert 'names.append("旁白")' not in route_source
    assert 'import TtsAgentWorkspace from "./ttsAgent/TtsAgentWorkspace.jsx";' in overlay_source
    assert 'import { createTtsAgentController } from "./ttsAgent/ttsAgentModel.js";' in overlay_source


def test_tts_agent_prompt_guide_keeps_keywords_per_scenario() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    workspace_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentWorkspace.jsx").read_text()

    assert "scenarioSelections" in model_source
    assert "function normalizeScenarioSelections(source = {})" in model_source
    assert "function flattenScenarioSelections(selections = {})" in model_source
    assert "function guideScenarioWords(current, scenarioId = \"\")" in model_source
    assert "function guideAllWords(current)" in model_source
    assert "scenario_id: scenarioId," in model_source
    assert "const savedSelections = normalizeScenarioSelections(source)" in model_source
    assert "scenario_selections: scenarioSelections" in model_source
    assert "...guideSeedForScenario(current.kind, scenarioId, {})" not in model_source
    assert "return current.kind === \"role\"" not in model_source
    assert ": uniqueWords(current.keywords || [])" not in model_source
    assert "controller.guideSelectedWords(scenario.id)" in workspace_source
    assert "controller.removeGuideKeyword(word, scenario.id)" in workspace_source
    assert "controller.clearGuideKeywords(scenario.id)" in workspace_source


def test_tts_agent_json_only_audio_cards_can_move_to_trash() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    workspace_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentWorkspace.jsx").read_text()
    overlay_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx").read_text()
    route_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/asset_routes.py").read_text()

    assert "function sessionJsonPath(value)" in model_source
    assert "json_path: jsonPath" in model_source
    assert "agent_session_path: jsonPath" in model_source
    assert "function removeAgentSession(sessionId)" in model_source
    assert "removeAgentSession," in model_source
    assert "agent_session_path: jsonPath" in workspace_source
    assert "json_path: jsonPath" in workspace_source
    assert "const assetTrashPath = (asset) => asset?.path || asset?.audio_path || asset?.history_path || asset?.agent_session_path || asset?.json_path || asset?.id || \"\";" in workspace_source
    assert "await props.onMoveAudioToHistory?.(asset);" in workspace_source
    assert "controller.removeAgentSession?.(currentSession.id)" in workspace_source
    assert "item?.agent_session_path" in overlay_source
    assert "item?.tts_agent_session?.json_path" in overlay_source
    assert "const assetId = asset?.id || asset?.path || asset?.audio_path || asset?.agent_session_path || asset?.json_path;" in overlay_source
    assert "moving_sidecar_only = (" in route_source
    assert 'item["json_only"] = True' in route_source


def test_tts_agent_json_only_history_items_can_restore() -> None:
    route_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/asset_routes.py").read_text()

    assert 'json_only_audio = suffix == ".json"' in route_source
    assert 'if suffix not in IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS and not json_only_audio:' in route_source
    assert 'sidecar_target = unique_sibling_path(target_dir / deps.safe_name(file_path.name, file_path.name))' in route_source
    assert '"missing_audio": not audio_exists' in route_source
    assert '"json_only": True' in route_source
    assert 'asset = uploaded_asset_payload(audio_path, "history_restore"' in route_source


def test_tts_agent_draft_polling_does_not_block_event_loop() -> None:
    route_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/tts_routes.py").read_text()

    assert "messages = await asyncio.to_thread(client.messages, opencode_session_id, limit=120)" in route_source
    assert "await asyncio.sleep(1)" in route_source
    assert "time.sleep(1)" not in route_source


def test_tts_agent_stream_failures_restore_generation_state() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()

    assert "try {\n      await api.streamCompareAssetTTS" in model_source
    assert "failed = error instanceof Error ? error.message : String(error);" in model_source
    assert 'if (!options.previewOnly) setAudioState("error");' in model_source
    assert "finally {\n      setGeneratingDialogueId(\"\");\n    }" in model_source


def test_tts_agent_uses_task_scoped_tts_config_for_non_admin_users() -> None:
    api_source = (ROOT / "frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js").read_text()
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    route_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/asset_routes.py").read_text()

    assert "assetLibraryTtsModelConfig" in api_source
    assert "/asset-library/tts-model-config" in api_source
    assert "api.assetLibraryTtsModelConfig(task().id)" in model_source
    assert '@router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/tts-model-config")' in route_source
    assert '"api_key_ref"' in route_source


def test_tts_agent_chat_messages_are_task_scoped_and_persisted() -> None:
    api_source = (ROOT / "frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js").read_text()
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    route_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/asset_routes.py").read_text()

    assert "TTS_AGENT_MESSAGES_REL" in route_source
    assert "upload_asset_library_tts_agent_messages_0.1" in route_source
    assert '@router.get("/api/koubo-storyboard/tasks/{task_id}/asset-library/tts-agent/messages")' in route_source
    assert '@router.put("/api/koubo-storyboard/tasks/{task_id}/asset-library/tts-agent/messages")' in route_source
    assert "assetLibraryTtsAgentMessages" in api_source
    assert "saveAssetLibraryTtsAgentMessages" in api_source
    assert "normalizeMessages" in model_source
    assert "persistMessages(nextMessages)" in model_source
    assert "loadPersistedMessages(taskId)" in model_source
    assert "api.assetLibraryTtsAgentMessages(taskId)" in model_source
    assert 'if (workspaceMode() !== "library" || activeSessionId()) return;' in model_source
    assert "commitMessages(saved, { persist: false });" in model_source


def test_tts_agent_hides_downstream_video_usage_status() -> None:
    overlay_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx").read_text()
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()
    workspace_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/TtsAgentWorkspace.jsx").read_text()

    assert "meta={meta}" in (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage.jsx").read_text()
    assert "meta: props.meta" in overlay_source
    assert "onNavigateToView: setView" in overlay_source
    assert "storyboard_video_slots?.by_asset_key" not in model_source
    assert "downstreamStatus" not in model_source
    assert "finalStaleAfterAudio" not in model_source
    assert "下游使用状态" not in workspace_source
    assert "去视频智能体" not in workspace_source
    assert "查看视频生成" not in workspace_source


def test_scene_tts_writes_back_by_dialogue_asset_key_when_available() -> None:
    route_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/tts_routes.py").read_text()
    service_source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/tts_workflow_services.py").read_text()

    assert "dialogue_asset_key: str = \"\"" in service_source
    assert 'text(dialogue.get("dialogue_asset_key")) != dialogue_asset_key' in service_source
    assert 'deps.update_dialogue_audio_path(workspace, task, deps.text(payload.get("dialogue_id")), deps.text(result.get("output")), deps.text(payload.get("dialogue_asset_key"))' in route_source
    assert 'deps.update_dialogue_audio_path(workspace, task, deps.text(payload.get("dialogue_id")), deps.text(cached_payload.get("output")), deps.text(payload.get("dialogue_asset_key"))' in route_source


def test_tts_agent_outputs_to_asset_audio_session_file() -> None:
    model_source = (ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/ttsAgent/ttsAgentModel.js").read_text()

    assert 'const ASSET_AUDIO_REL = "SessionOutput/storyboard/assets/audios"' in model_source
    assert 'const sessionOutput = !options.previewOnly && activeSessionId() ? `${dir}/${safeName(activeSessionId(), "tts_agent_audio")}.${ext}` : ""' in model_source
    assert 'output: sessionOutput || `${dir}/${name}.${ext}`' in model_source
    assert "SessionOutput/storyboard/Working/${dialogue.dialogue_asset_key}/Audio_Final.${ext}" not in model_source
    assert "SessionOutput/storyboard/Working/${dialogue.dialogue_asset_key}_Audio_Final.${ext}" not in model_source


def test_scene_tts_uses_active_provider_defaults_for_non_admin_generation() -> None:
    source = (ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/tts_routes.py").read_text()

    assert "def active_tts_generation_defaults" in source
    assert 'load_config(deps.ctx, "tts")' in source
    assert "defaults = active_tts_generation_defaults(prompt_item)" in source
    assert '"voice_id": defaults.get("voice_id") or deps.text(prompt_item.get("voice_id"))' in source
