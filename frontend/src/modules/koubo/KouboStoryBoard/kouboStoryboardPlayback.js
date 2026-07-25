export function createKouboStoryboardPlaybackController(deps) {
  const {
    shots,
    scope,
    selectedShotIndex,
    selectedDialogueId,
    playbackState,
    setPlaybackState,
    playbackSpeed,
    setPlaybackSpeed,
    setPlaybackSpeedOpen,
    setError,
    scrollToNode,
    generateSceneAudio,
    applySceneAudioDuration,
    cancelActiveTtsRequests,
    selectScene,
  } = deps;
  let playbackTimer = null;
  let playbackRunId = 0;
  let storyboardAudio = null;
  let activeAudioResolve = null;

  function playAudioSource(src) {
    if (!src) return Promise.reject(new Error("没有可播放的音频"));
    if (storyboardAudio) storyboardAudio.pause();
    storyboardAudio = new Audio(src);
    storyboardAudio.playbackRate = playbackSpeed();
    return new Promise((resolve, reject) => {
      activeAudioResolve = resolve;
      storyboardAudio.onended = () => {
        activeAudioResolve = null;
        resolve(storyboardAudio.duration || 0);
      };
      storyboardAudio.onerror = () => {
        activeAudioResolve = null;
        reject(new Error("音频播放失败"));
      };
      void storyboardAudio.play().catch((err) => {
        activeAudioResolve = null;
        reject(err);
      });
    });
  }

  function isBenignTtsStop(err) {
    const message = err instanceof Error ? err.message : String(err || "");
    return /TTS 试听已停止|TTS 试听已因音色设置变更而停止/.test(message);
  }

  async function playAudioSequence(audio) {
    const items = Array.isArray(audio?.items) && audio.items.length ? audio.items : [audio];
    let totalDuration = 0;
    for (const item of items) {
      if (!item?.audioSrc) continue;
      totalDuration += await playAudioSource(item.audioSrc);
    }
    return totalDuration;
  }

  function allScenesWithShots() {
    return shots().flatMap((shot, shotIndex) => (shot.scenes || []).map((scene) => ({ shot, shotIndex, scene })));
  }

  function scenesForPlayback() {
    const allScenes = allScenesWithShots();
    if (!allScenes.length) return [];
    if (scope() === "all") return allScenes;
    if (scope() === "shot") return allScenes.filter((item) => item.shotIndex === selectedShotIndex());
    const selected = selectedDialogueId();
    const found = allScenes.find((item) => (item.scene.dialogues || []).some((dialogue) => dialogue.dialogue_id === selected));
    return found ? [found] : allScenes.filter((item) => item.shotIndex === selectedShotIndex());
  }

  function stopTimelinePlayback() {
    playbackRunId += 1;
    cancelActiveTtsRequests?.("cancelled");
    if (playbackTimer) window.clearTimeout(playbackTimer);
    playbackTimer = null;
    storyboardAudio?.pause();
    if (activeAudioResolve) {
      activeAudioResolve(0);
      activeAudioResolve = null;
    }
    setPlaybackState({ phase: "idle", status: "", currentShotId: "", currentSceneId: "" });
  }

  async function startTimelinePlayback() {
    const queue = scenesForPlayback();
    if (!queue.length) return;
    const runId = playbackRunId + 1;
    playbackRunId = runId;
    setPlaybackSpeedOpen(false);
    try {
      for (let index = 0; index < queue.length; index += 1) {
        if (playbackRunId !== runId) return;
        const item = queue[index];
        const status = `${index + 1}/${queue.length} · ${item.shot.shot_name || item.shot.shot_id} · Scene ${item.scene.scene_index || index + 1}`;
        setPlaybackState({
          phase: "generating",
          status,
          currentShotId: item.shot.shot_id,
          currentSceneId: item.scene.scene_id,
        });
        scrollToNode(`kbsp-scene-${item.scene.scene_id}`);
        const audio = await generateSceneAudio(item.shot, item.scene);
        if (playbackRunId !== runId) return;
        setPlaybackState({
          phase: "playing",
          status,
          currentShotId: item.shot.shot_id,
          currentSceneId: item.scene.scene_id,
        });
        const duration = await playAudioSequence(audio);
        if (playbackRunId !== runId) return;
        applySceneAudioDuration(item.scene.scene_id, audio.durationSeconds || duration, audio.items);
      }
      if (playbackRunId === runId) stopTimelinePlayback();
    } catch (err) {
      console.error(err);
      if (playbackRunId === runId) {
        if (isBenignTtsStop(err)) {
          setPlaybackState({ phase: "idle", status: "", currentShotId: "", currentSceneId: "" });
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
        setPlaybackState({ phase: "error", status: "", currentShotId: "", currentSceneId: "" });
      }
    }
  }

  async function playSceneTTS(shotIndex, shot, scene) {
    if (!shot || !scene?.scene_id) return;
    const currentPhase = playbackState().phase;
    const isCurrentScene = playbackState().currentSceneId === scene.scene_id;
    if (isCurrentScene && currentPhase === "playing") {
      storyboardAudio?.pause();
      setPlaybackState((previous) => ({ ...previous, phase: "paused" }));
      return;
    }
    if (isCurrentScene && currentPhase === "paused" && storyboardAudio) {
      storyboardAudio.playbackRate = playbackSpeed();
      await storyboardAudio.play();
      setPlaybackState((previous) => ({ ...previous, phase: "playing" }));
      return;
    }
    if (isCurrentScene && currentPhase === "generating") return;
    stopTimelinePlayback();
    selectScene(shotIndex, scene);
    setPlaybackSpeedOpen(false);
    setError("");
    const runId = playbackRunId + 1;
    playbackRunId = runId;
    const status = `1/1 · ${shot.shot_name || shot.shot_id} · Scene ${scene.scene_index || 1}`;
    try {
      setPlaybackState({ phase: "generating", status, currentShotId: shot.shot_id, currentSceneId: scene.scene_id });
      scrollToNode(`kbsp-scene-${scene.scene_id}`);
      const audio = await generateSceneAudio(shot, scene);
      if (playbackRunId !== runId) return;
      setPlaybackState({ phase: "playing", status, currentShotId: shot.shot_id, currentSceneId: scene.scene_id });
      const duration = await playAudioSequence(audio);
      if (playbackRunId !== runId) return;
      applySceneAudioDuration(scene.scene_id, audio.durationSeconds || duration, audio.items);
      if (playbackRunId === runId) stopTimelinePlayback();
    } catch (err) {
      console.error(err);
      if (playbackRunId === runId) {
        if (isBenignTtsStop(err)) {
          setPlaybackState({ phase: "idle", status: "", currentShotId: "", currentSceneId: "" });
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
        setPlaybackState({ phase: "error", status: "", currentShotId: "", currentSceneId: "" });
      }
    }
  }

  async function toggleTimelinePlayback() {
    const phase = playbackState().phase;
    if (phase === "playing") {
      storyboardAudio?.pause();
      setPlaybackState((previous) => ({ ...previous, phase: "paused" }));
      return;
    }
    if (phase === "paused") {
      if (storyboardAudio) {
        storyboardAudio.playbackRate = playbackSpeed();
        await storyboardAudio.play();
        setPlaybackState((previous) => ({ ...previous, phase: "playing" }));
      }
      return;
    }
    if (phase === "generating") {
      stopTimelinePlayback();
      return;
    }
    await startTimelinePlayback();
  }

  function applyPlaybackSpeed(value) {
    const next = Math.min(4, Math.max(0.25, Number(value || 1)));
    setPlaybackSpeed(Number.isFinite(next) ? Number(next.toFixed(2)) : 1);
    if (storyboardAudio) storyboardAudio.playbackRate = Number.isFinite(next) ? Number(next.toFixed(2)) : 1;
    setPlaybackSpeedOpen(false);
  }

  function pauseActiveAudio() {
    storyboardAudio?.pause();
  }

  return {
    playAudioSource,
    allScenesWithShots,
    scenesForPlayback,
    stopTimelinePlayback,
    startTimelinePlayback,
    playSceneTTS,
    toggleTimelinePlayback,
    applyPlaybackSpeed,
    pauseActiveAudio,
  };
}
