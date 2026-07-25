from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]


class KouboStoryboardDefaultRouteContractTest(unittest.TestCase):
    def test_storyboard_fallback_routes_to_list_not_hardcoded_task(self) -> None:
        model_source = (REPO_ROOT / "frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardModel.js").read_text(encoding="utf-8")
        fallback_route = model_source.split("if (!taskMatch)", 1)[1].split(
            "const queryText", 1
        )[0]
        self.assertIn('view: "list"', fallback_route)
        self.assertIn("taskId: 0", fallback_route)
        self.assertIn('dialogueAssetKey: ""', fallback_route)
        self.assertIn('navigationError: ""', fallback_route)
        self.assertNotIn("#/koubo-storyboard/tasks/31", model_source)

        route_sources = [
            REPO_ROOT / "frontend/src/App.jsx",
            REPO_ROOT / "frontend/src/modules/koubo/KouboStoryBoardModule.jsx",
            REPO_ROOT / "frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage.jsx",
            REPO_ROOT / "frontend/src/modules/koubo/KouboStoryBoard/components/AssetPanel.jsx",
        ]
        for path in route_sources:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("#/koubo-storyboard/tasks/31", source)
                self.assertNotIn('|| "31"', source)

    def test_tts_builder_candidates_use_customer_safe_task_endpoint(self) -> None:
        module_source = (REPO_ROOT / "frontend/src/modules/koubo/KouboStoryBoardModule.jsx").read_text(encoding="utf-8")
        api_source = (REPO_ROOT / "frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js").read_text(encoding="utf-8")
        analysis_module_source = (REPO_ROOT / "frontend/src/modules/koubo/AnalysisV1/AnalysisV1Module.jsx").read_text(encoding="utf-8")
        analysis_api_source = (REPO_ROOT / "frontend/src/modules/koubo/AnalysisV1/analysisV1Api.js").read_text(encoding="utf-8")
        analysis_builder_source = (REPO_ROOT / "frontend/src/modules/koubo/AnalysisV1/components/AnalysisV1TTSBuilder.jsx").read_text(encoding="utf-8")
        quick_state_section = analysis_builder_source.split("async function loadQuickAdvState", 1)[1].split("async function runQuickAdvCatalog", 1)[0]
        route_source = (REPO_ROOT / "backend/opcrew_backend/koubo/koubo_storyboard/task_routes.py").read_text(encoding="utf-8")

        self.assertIn("kbApi.ttsBuilderCandidates(taskId)", module_source)
        self.assertNotIn('rawFileUrl(sessionId, "SessionOutput/tts/tts_builder_candidates.json")', module_source)
        self.assertIn("ttsBuilderCandidates: (taskId)", api_source)
        self.assertIn("analysisV1Api.ttsBuilderCandidates(taskId)", analysis_module_source)
        self.assertNotIn("readWorkspaceJson(sessionId, TTS_BUILDER_CANDIDATES_PATH)", analysis_module_source)
        self.assertIn("ttsBuilderCandidates: async (taskId)", analysis_api_source)
        self.assertIn("props.api.ttsBuilderCandidates(props.taskId)", analysis_builder_source)
        self.assertIn("requires_cloud_clone_refresh", analysis_builder_source)
        self.assertIn("requiresCloudCloneRefresh()", analysis_builder_source)
        self.assertIn("setRequiresCloudCloneRefresh", analysis_builder_source)
        self.assertIn("await reloadCandidatesFromSession()", quick_state_section)
        self.assertNotIn("setLocalTtsPayload", quick_state_section)
        self.assertIn('/tts-builder-candidates")', route_source)
        self.assertIn("normalize_storyboard_tts_selection", route_source)
        self.assertIn("storyboard_tts_candidate_is_inactive_cloud_clone", route_source)
        self.assertIn("has_active_cloud_candidate", route_source)
        self.assertIn("bool(inactive_cloud_candidate_count and not has_active_cloud_candidate)", route_source)
        self.assertIn('"requires_cloud_clone_refresh"', route_source)


if __name__ == "__main__":
    unittest.main()
