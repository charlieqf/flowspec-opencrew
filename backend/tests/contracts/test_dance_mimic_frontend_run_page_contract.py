from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
APP_CONTROLLER_SOURCE = REPO_ROOT / "frontend/src/shell/useOpenCrewAppController.jsx"
APP_ROUTING_CONTROLLER_SOURCE = REPO_ROOT / "frontend/src/shell/controllers/useShellRoutingController.jsx"
APP_VIEW_SOURCE = REPO_ROOT / "frontend/src/shell/OpenCrewShellView.jsx"
APP_RIGHT_SIDEBAR_SOURCE = REPO_ROOT / "frontend/src/shell/AppRightSidebar.jsx"
KOUBO_STORYBOARD_MODULE = REPO_ROOT / "frontend/src/modules/koubo/KouboStoryBoardModule.jsx"
DANCE_MIMIC_DIR = REPO_ROOT / "frontend/src/modules/koubo/DanceMimicV1"
SHARED_DIR = REPO_ROOT / "frontend/src/modules/koubo/shared"
TASK_LIST_DIR = REPO_ROOT / "frontend/src/modules/koubo/KouboTaskList"


class DanceMimicFrontendRunPageContractTest(unittest.TestCase):
    def test_app_routes_dance_mimic_hash_to_independent_module(self) -> None:
        controller_source = (
            APP_CONTROLLER_SOURCE.read_text(encoding="utf-8")
            + "\n"
            + APP_ROUTING_CONTROLLER_SOURCE.read_text(encoding="utf-8")
        )
        view_source = APP_VIEW_SOURCE.read_text(encoding="utf-8")
        sidebar_source = APP_RIGHT_SIDEBAR_SOURCE.read_text(encoding="utf-8")

        self.assertIn("DanceMimicV1Module", view_source)
        self.assertIn("DanceMimicV1MediaSidebar", view_source)
        self.assertIn('hashValue.startsWith("#/dance-mimic")', controller_source)
        self.assertIn('initialHash.startsWith("#/dance-mimic")', controller_source)
        self.assertIn('nextHash.startsWith("#/dance-mimic")', controller_source)
        self.assertIn('activeNav() === "dance-mimic" ? <DanceMimicV1Module routeHash={routeHash()} onMediaItemChange={setDanceMimicMediaItem} />', view_source)
        self.assertIn('activeNav() === "dance-mimic" ? <DanceMimicV1MediaSidebar item={danceMimicMediaItem()} />', sidebar_source)
        self.assertNotIn('activeNav() === "dance-mimic" ? <AnalysisV1Module', view_source)
        self.assertNotIn('activeNav() === "dance-mimic" ? <AnalysisV1Module', sidebar_source)

    def test_run_page_uses_only_dance_mimic_v1_surface(self) -> None:
        api_source = (DANCE_MIMIC_DIR / "danceMimicV1Api.js").read_text(encoding="utf-8")
        module_source = (DANCE_MIMIC_DIR / "DanceMimicV1Module.jsx").read_text(encoding="utf-8")
        style_source = (DANCE_MIMIC_DIR / "danceMimicV1.css").read_text(encoding="utf-8")
        run_dialog_source = (SHARED_DIR / "RunProgressDialog.jsx").read_text(encoding="utf-8")
        combined = api_source + "\n" + module_source

        self.assertIn("/api/dance-mimic-v1/tasks/${taskId}", api_source)
        self.assertIn("/api/dance-mimic-v1/tasks/${taskId}/run", api_source)
        self.assertIn("/api/dance-mimic-v1/tasks/${taskId}/run/${attemptId}", api_source)
        self.assertIn("latest_run", module_source)
        self.assertIn("reference_video_path", module_source)
        self.assertIn("target_identity_image_path", module_source)
        self.assertIn("target_identity_image", module_source)
        self.assertIn("reference_privacy_mode", module_source)
        self.assertIn("参考隐私", module_source)
        self.assertIn("const [paramsCollapsed, setParamsCollapsed] = createSignal(true);", module_source)
        self.assertIn("RunProgressDialog", module_source)
        self.assertIn("dmv1-run-artifacts", module_source)
        self.assertIn("逐句动作模拟拆解", module_source)
        self.assertIn("const [storyboardCollapsed, setStoryboardCollapsed] = createSignal(true);", module_source)
        self.assertIn("storyboardCollapsed", module_source)
        self.assertIn("setStoryboardCollapsed((value) => !value)", module_source)
        self.assertIn("setParamsCollapsed(true)", module_source)
        self.assertIn("setStoryboardCollapsed(true)", module_source)
        self.assertIn('title={storyboardCollapsed() ? "展开逐句动作模拟拆解" : "收起逐句动作模拟拆解"}', module_source)
        self.assertIn("storyboardItems", module_source)
        self.assertIn("selectedDialogue", module_source)
        self.assertIn("遮脸参考视频", module_source)
        self.assertIn("一键成片结果", module_source)
        self.assertIn("sidebarItemFromDialogue", module_source)
        self.assertIn("item?.reference_video?.preview_url", module_source)
        self.assertIn("function rawFileUrl", module_source)
        self.assertIn("movieOutputVideoUrl", module_source)
        self.assertIn('props.onMediaItemChange?.(sidebarItemFromDialogue(selectedDialogue(), { outputVideo: movieOutputVideo(), outputVideoUrl: movieOutputVideoUrl() }))', module_source)
        self.assertNotIn("<small>{props.item.movieOutputVideo}</small>", module_source)
        self.assertIn("dmv1-storyboard-grid", style_source)
        self.assertIn("dmv1-dialogue-list", style_source)
        self.assertIn("dmv1-storyboard-collapse", style_source)
        self.assertIn("dmv1-media-sidebar", style_source)
        self.assertIn("dmv1-movie-output-card", style_source)
        self.assertNotIn('<aside class="dmv1-reference-preview">', module_source)
        self.assertIn("准备运行参数", module_source)
        self.assertIn("拆解参考视频", module_source)
        self.assertIn("处理人脸合规", module_source)
        self.assertIn("构建故事版", module_source)
        self.assertNotIn("StoryBoard 构建", module_source)
        self.assertNotIn("故事板构建", module_source)
        self.assertNotIn("故事版构建", module_source)
        self.assertIn("HIDDEN_RUN_ARTIFACT_KEYS", module_source)
        self.assertIn('"variables_json"', module_source)
        self.assertIn('"source_reference_video"', module_source)
        self.assertIn('"target_identity_image"', module_source)
        self.assertIn("!HIDDEN_RUN_ARTIFACT_KEYS.has(key)", module_source)
        self.assertIn("RUN_ARTIFACT_ORDER", module_source)
        self.assertIn('"reference_media_manifest", "reference_segments_manifest", "srt_storyboard", "storyboard_seed", "stale_manifest"', module_source)
        self.assertIn("参考媒体清单", module_source)
        self.assertIn("人脸合规清单", module_source)
        self.assertIn("故事版对白", module_source)
        self.assertIn("分配故事版记录", module_source)
        self.assertIn("素材失效清单", module_source)
        self.assertIn("dmv1-artifact-index", module_source)
        self.assertIn("index() + 1", module_source)
        self.assertIn("dmv1-artifact-status", module_source)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", style_source)
        self.assertIn("font-size: 13px;", style_source)
        self.assertIn("overflow-wrap: anywhere;", style_source)
        self.assertNotIn("text-overflow: ellipsis;", style_source)
        self.assertNotIn("参考媒体 manifest", module_source)
        self.assertNotIn("遮脸分段 manifest", module_source)
        self.assertNotIn("遮脸分段清单", module_source)
        self.assertNotIn("SRT 故事板", module_source)
        self.assertNotIn("故事版种子", module_source)
        self.assertNotIn("故事板种子", module_source)
        self.assertNotIn("合规实效清单", module_source)
        self.assertNotIn("StoryBoard seed", module_source)
        self.assertNotIn("Stale manifest", module_source)
        self.assertNotIn("{item.path}", module_source)
        self.assertNotIn('item.exists ? formatSize(item.size) : "-"', module_source)
        self.assertIn("setRunDialogOpen(true)", module_source)
        self.assertIn('const ERROR_STATUSES = new Set(["failed", "blocked", "cancelled"])', module_source)
        self.assertIn("ERROR_STATUSES.has", module_source)
        self.assertIn("startedAt={latestRun()?.started_at}", module_source)
        self.assertIn("finishedAt={latestRun()?.finished_at}", module_source)
        self.assertIn("totalDurationSeconds={latestRun()?.duration_seconds}", module_source)
        self.assertNotIn("setNotice", module_source)
        self.assertNotIn("dmv1-banner good", module_source)
        self.assertIn("timestampDurationSeconds(step?.started_at, step?.finished_at)", run_dialog_source)
        self.assertIn("props.totalDurationSeconds", run_dialog_source)
        self.assertIn("force: Boolean(runOptions.force)", module_source)
        self.assertIn("startRun({ force: true })", module_source)
        self.assertNotIn("force: true,", module_source)
        self.assertNotIn("/api/analysis-v1", combined)
        self.assertNotIn("/api/openclip/tasks", combined)
        self.assertNotIn("AnalysisV1Module", module_source)

    def test_storyboard_pro_reads_dance_mimic_session_variables_without_analysis_v1_run(self) -> None:
        source = KOUBO_STORYBOARD_MODULE.read_text(encoding="utf-8")

        self.assertIn('const DANCE_MIMIC_WORKFLOW_ID = "dance_mimic_v1"', source)
        self.assertIn("const isDanceMimicTask = createMemo(", source)
        self.assertIn("task()?.workflow_mode", source)
        self.assertIn("meta()?.workflow_mode", source)
        self.assertIn("正在读取 DanceMimic Session Variables...", source)
        self.assertIn("if (isDanceMimicTask())", source)
        self.assertIn("kbApi.readWorkspaceJson(currentSessionId, SESSION_VARIABLES_PATH)", source)

    def test_create_flow_navigates_to_dance_mimic_page(self) -> None:
        page_source = (TASK_LIST_DIR / "KouboTaskListPage.jsx").read_text(encoding="utf-8")

        self.assertIn("saveDanceMimicTask", page_source)
        self.assertIn('window.location.hash = `#/dance-mimic/tasks/${taskId}`', page_source)


if __name__ == "__main__":
    unittest.main()
