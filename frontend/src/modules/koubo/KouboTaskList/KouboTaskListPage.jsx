import { createMemo, createSignal, onMount } from "solid-js";
import { kouboTaskListApi } from "./kouboTaskListApi.js";
import { filterTasks, normalizeTask } from "./kouboTaskListModel.js";
import KouboTaskCreateDanceMimicModal from "./KouboTaskCreateDanceMimicModal.jsx";
import KouboTaskCreateFromScriptModal from "./KouboTaskCreateFromScriptModal.jsx";
import KouboTaskCreateFromVideoModal from "./KouboTaskCreateFromVideoModal.jsx";
import KouboTaskCreateMenu from "./KouboTaskCreateMenu.jsx";
import KouboTaskFilters from "./KouboTaskFilters.jsx";
import KouboTaskListTable from "./KouboTaskListTable.jsx";
import "./styles/taskListPage.css";
import "./styles/taskListTable.css";
import "./styles/taskCreateModal.css";
import "./styles/taskStatusBadges.css";

const TASK_LIST_AUTO_RUN_KEY = "koubo_task_list_auto_run";
const TASK_LIST_AUTO_ANALYSIS_DRAWER_KEY = "koubo_task_list_auto_analysis_drawer";

export default function KouboTaskListPage() {
  const [items, setItems] = createSignal([]);
  const [filters, setFilters] = createSignal({ keyword: "", status: "all", mode: "all" });
  const [includeArchived, setIncludeArchived] = createSignal(false);
  const [busy, setBusy] = createSignal("");
  const [error, setError] = createSignal("");
  const [notice, setNotice] = createSignal("");
  const [videoModalOpen, setVideoModalOpen] = createSignal(false);
  const [danceMimicModalOpen, setDanceMimicModalOpen] = createSignal(false);
  const [scriptModalOpen, setScriptModalOpen] = createSignal(false);
  const [scriptProfile, setScriptProfile] = createSignal({ id: "script", createMode: "script" });
  const [selectedTaskId, setSelectedTaskId] = createSignal(null);
  // Freeze the task being edited when the modal opens. The table selection can
  // change while prompt generation reloads the list, but that must never turn
  // an update into a create operation.
  const [scriptTaskId, setScriptTaskId] = createSignal(null);
  const [taskDetails, setTaskDetails] = createSignal({});
  const [promptOptions, setPromptOptions] = createSignal({});
  const [promptModels, setPromptModels] = createSignal({ items: [], default_model: { providerID: "", modelID: "" } });

  const visibleItems = createMemo(() => filterTasks(items(), filters()));
  const selectedTask = createMemo(() => {
    const taskId = selectedTaskId();
    if (!taskId) return null;
    return taskDetails()[taskId] || items().find((item) => item.taskId === taskId) || null;
  });
  const scriptTask = createMemo(() => {
    const taskId = scriptTaskId();
    if (!taskId) return null;
    return taskDetails()[taskId] || items().find((item) => item.taskId === taskId) || null;
  });
  const selectedDanceMimicTask = createMemo(() => {
    const task = selectedTask();
    return task?.createMode === "dance_mimic" ? task : null;
  });

  function patchFilters(patch) {
    setFilters((prev) => ({ ...prev, ...patch }));
  }

  async function load() {
    setBusy("load");
    setError("");
    try {
      const payload = await kouboTaskListApi.list(includeArchived());
      const nextItems = (payload.items || []).map(normalizeTask);
      setItems(nextItems);
      setTaskDetails((previous) => {
        const byId = new Map(nextItems.map((item) => [item.taskId, item]));
        return Object.fromEntries(Object.entries(previous).filter(([taskId, detail]) => {
          const summary = byId.get(Number(taskId));
          return summary && Number(detail?.updatedAt || 0) === Number(summary.updatedAt || 0);
        }));
      });
      if (selectedTaskId() && !nextItems.some((item) => item.taskId === selectedTaskId())) setSelectedTaskId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载任务列表失败");
    } finally {
      setBusy("");
    }
  }

  async function loadTaskDetail(taskId) {
    const numericTaskId = Number(taskId || 0);
    if (!numericTaskId) return null;
    const summary = items().find((item) => item.taskId === numericTaskId);
    const cached = taskDetails()[numericTaskId];
    if (cached && (!summary || Number(cached.updatedAt || 0) === Number(summary.updatedAt || 0))) return cached;
    const payload = await kouboTaskListApi.detail(numericTaskId);
    const detail = normalizeTask(payload.item || {});
    setTaskDetails((previous) => ({ ...previous, [numericTaskId]: detail }));
    return detail;
  }

  async function openDanceMimicModal() {
    const taskId = selectedDanceMimicTask()?.taskId;
    if (!taskId) {
      setDanceMimicModalOpen(true);
      return;
    }
    setBusy(`detail-${taskId}`);
    setError("");
    try {
      await loadTaskDetail(taskId);
      setDanceMimicModalOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载任务详情失败");
    } finally {
      setBusy("");
    }
  }

  async function openScriptModal(profile) {
    setScriptProfile(profile);
    void loadPromptOptions();
    const taskId = selectedTaskId();
    setScriptTaskId(taskId || null);
    if (!taskId) {
      setScriptModalOpen(true);
      return;
    }
    setBusy(`detail-${taskId}`);
    setError("");
    try {
      await loadTaskDetail(taskId);
      setScriptModalOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载任务详情失败");
    } finally {
      setBusy("");
    }
  }

  async function createVideoTask() {
    setBusy("create-video");
    setError("");
    try {
      const detail = await kouboTaskListApi.createFromVideo();
      const taskId = detail?.task?.id;
      setVideoModalOpen(false);
      await load();
      if (taskId) {
        window.sessionStorage?.setItem(TASK_LIST_AUTO_ANALYSIS_DRAWER_KEY, JSON.stringify({ taskId, createdAt: Date.now() }));
        window.location.hash = `#/analysis-v1/tasks/${taskId}`;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建视频任务失败");
    } finally {
      setBusy("");
    }
  }

  async function saveDanceMimicTask(payload, options = {}) {
    setBusy("create-dance-mimic");
    setError("");
    setNotice("");
    try {
      const targetTaskId = options.taskId || selectedDanceMimicTask()?.taskId;
      const detail = targetTaskId
        ? await kouboTaskListApi.updateDanceMimic(targetTaskId, payload)
        : await kouboTaskListApi.createDanceMimic(payload);
      const taskId = detail?.task_id;
      setDanceMimicModalOpen(false);
      await load();
      if (taskId) {
        setSelectedTaskId(taskId);
        setNotice(targetTaskId ? `已更新动作模拟任务 #${taskId}` : (payload.auto_run ? `已创建并启动动作模拟任务 #${taskId}` : `已创建动作模拟任务 #${taskId}`));
        window.location.hash = `#/dance-mimic/tasks/${taskId}`;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存动作模拟任务失败");
    } finally {
      setBusy("");
    }
  }

  async function createScriptTask(payload, options = {}) {
    setBusy("create-script");
    setError("");
    try {
      const targetTaskId = options.taskId || scriptTaskId();
      const isTalkingHead = payload?.profile_id === "person_talking_head_v1" || payload?.create_mode === "person_talking_head";
      const res = isTalkingHead
        ? targetTaskId
          ? await kouboTaskListApi.updateTalkingHead(targetTaskId, payload)
          : await kouboTaskListApi.createTalkingHead(payload)
        : targetTaskId
        ? await kouboTaskListApi.updateFromScript(targetTaskId, payload)
        : await kouboTaskListApi.createFromScript(payload);
      const taskId = res?.task_id;
      setScriptModalOpen(false);
      await load();
      if (isTalkingHead && taskId) {
        setSelectedTaskId(taskId);
        setScriptTaskId(taskId);
        setNotice(targetTaskId ? `已更新人物口播任务 #${taskId}` : `已创建人物口播任务 #${taskId}`);
        if (options.action === "run_all") {
          window.location.hash = `#/talking-head/tasks/${taskId}`;
        }
        return;
      }
      if (options.action === "run_all" && taskId) {
        window.sessionStorage?.setItem(TASK_LIST_AUTO_RUN_KEY, JSON.stringify({ taskId, createdAt: Date.now() }));
        window.location.hash = `#/analysis-v1/tasks/${taskId}`;
        return;
      }
      setNotice(targetTaskId ? `已更新脚本任务 #${taskId}` : `已创建脚本任务 #${taskId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建脚本任务失败");
    } finally {
      setBusy("");
    }
  }

  async function loadPromptModels() {
    if (promptModels().items?.length) return promptModels();
    const models = await kouboTaskListApi.promptModels();
    setPromptModels(models);
    return models;
  }

  async function loadPromptOptions() {
    if (promptOptions().industry?.length) return promptOptions();
    const options = await kouboTaskListApi.options();
    setPromptOptions(options || {});
    return options;
  }

  async function savePromptTask(payload) {
    const targetTaskId = scriptTaskId();
    const isTalkingHead = payload?.profile_id === "person_talking_head_v1" || payload?.create_mode === "person_talking_head";
    if (isTalkingHead) {
      return targetTaskId
        ? kouboTaskListApi.updateTalkingHead(targetTaskId, payload)
        : kouboTaskListApi.createTalkingHead(payload);
    }
    return targetTaskId
      ? kouboTaskListApi.updateFromScript(targetTaskId, payload)
      : kouboTaskListApi.createFromScript(payload);
  }

  async function generateScriptPrompts(payload, model) {
    setBusy("generate-script-prompts");
    setError("");
    try {
      const saved = await savePromptTask(payload);
      const taskId = saved?.task_id;
      if (!taskId) throw new Error("保存脚本任务失败，无法生成复杂提示词");
      setSelectedTaskId(taskId);
      setScriptTaskId(taskId);
      const promptRequest = {
        prompt_model_provider: model?.providerID || "",
        prompt_model_id: model?.modelID || "",
      };
      await kouboTaskListApi.generatePrompt(taskId, { ...promptRequest, prompt_kind: "rewrite" });
      const detail = await kouboTaskListApi.generatePrompt(taskId, { ...promptRequest, prompt_kind: "storyboard" });
      await load();
      setNotice(`已生成 Task #${taskId} 的全部复杂提示词`);
      return detail;
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成复杂提示词失败");
      throw err;
    } finally {
      setBusy("");
    }
  }

  async function generateScriptFinalPrompt(payload, model) {
    setBusy("generate-script-prompts");
    setError("");
    try {
      const saved = await savePromptTask(payload);
      const taskId = saved?.task_id;
      if (!taskId) throw new Error("保存脚本任务失败，无法生成脚本最终提示词");
      setSelectedTaskId(taskId);
      setScriptTaskId(taskId);
      const detail = await kouboTaskListApi.generatePrompt(taskId, {
        prompt_model_provider: model?.providerID || "",
        prompt_model_id: model?.modelID || "",
        prompt_kind: "rewrite",
      });
      await load();
      setNotice(`已通过一次模型调用生成 Task #${taskId} 的脚本最终提示词`);
      return detail;
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成脚本最终提示词失败");
      throw err;
    } finally {
      setBusy("");
    }
  }

  async function archiveTask(item) {
    if (!window.confirm(`确认归档 Task #${item.taskId}？workspace 不会被删除。`)) return;
    setBusy(`archive-${item.taskId}`);
    try {
      await kouboTaskListApi.archive(item.taskId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "归档失败");
    } finally {
      setBusy("");
    }
  }

  async function deleteTask(item) {
    if (!window.confirm(`确认物理删除 Task #${item.taskId} / Session #${item.sessionId}？任务记录和 workspace 会被删除，不能恢复。`)) return;
    setBusy(`delete-${item.taskId}`);
    try {
      await kouboTaskListApi.delete(item.taskId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusy("");
    }
  }

  onMount(() => {
    void load();
    void loadPromptOptions();
  });

  return (
    <div class="koubo-task-list-page">
      <header class="koubo-task-list-header">
        <div>
          <h2>任务列表（口播）</h2>
        </div>
        <div class="koubo-task-list-header-actions">
          <KouboTaskCreateMenu
            onVideo={() => setVideoModalOpen(true)}
            onDanceMimic={() => void openDanceMimicModal()}
            onScript={() => void openScriptModal({ id: "script", createMode: "script" })}
            onTalkingHead={() => void openScriptModal({ id: "person_talking_head_v1", createMode: "person_talking_head" })}
          />
        </div>
      </header>

      {error() ? <div class="koubo-task-list-banner bad">{error()}</div> : null}
      {notice() ? <div class="koubo-task-list-banner good">{notice()}</div> : null}

      <KouboTaskFilters
        filters={filters}
        onChange={patchFilters}
        includeArchived={includeArchived}
        onIncludeArchivedChange={(value) => { setIncludeArchived(value); queueMicrotask(() => void load()); }}
      />
      <KouboTaskListTable items={visibleItems} selectedTaskId={selectedTaskId} onSelect={(item) => setSelectedTaskId((prev) => prev === item.taskId ? null : item.taskId)} onArchive={(item) => void archiveTask(item)} onDelete={(item) => void deleteTask(item)} />
      <KouboTaskCreateFromVideoModal open={videoModalOpen} busy={() => busy() === "create-video"} onClose={() => setVideoModalOpen(false)} onCreate={() => void createVideoTask()} />
      <KouboTaskCreateDanceMimicModal
        open={danceMimicModalOpen}
        task={selectedDanceMimicTask}
        busy={() => busy() === "create-dance-mimic"}
        onClose={() => setDanceMimicModalOpen(false)}
        onCreate={(payload, options) => void saveDanceMimicTask(payload, options)}
        onListReferenceVideos={() => kouboTaskListApi.listDanceMimicReferenceVideos()}
        onListTargetImages={() => kouboTaskListApi.listDanceMimicTargetImages()}
      />
      <KouboTaskCreateFromScriptModal
        open={scriptModalOpen}
        task={scriptTask}
        busy={() => busy() === "create-script"}
        promptBusy={() => busy() === "generate-script-prompts"}
        promptModels={promptModels}
        promptOptions={promptOptions}
        profile={scriptProfile}
        onLoadPromptModels={() => loadPromptModels()}
        onListVoiceClones={() => kouboTaskListApi.listTalkingHeadVoiceClones()}
        onPreviewVoiceClone={(payload) => kouboTaskListApi.previewTalkingHeadVoiceClone(payload)}
        onDeleteVoiceClone={(voiceId) => kouboTaskListApi.deleteTalkingHeadVoiceClone(voiceId)}
        onGeneratePrompts={(payload, model) => generateScriptPrompts(payload, model)}
        onGenerateScriptFinalPrompt={(payload, model) => generateScriptFinalPrompt(payload, model)}
        onClose={() => { setScriptModalOpen(false); setScriptTaskId(null); }}
        onCreate={(payload, options) => createScriptTask(payload, { ...options, taskId: scriptTaskId() })}
      />
    </div>
  );
}
