from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_LIST_DIR = REPO_ROOT / "frontend/src/modules/koubo/KouboTaskList"


class DanceMimicFrontendCreateContractTest(unittest.TestCase):
    def test_task_list_uses_real_dance_mimic_create_modal(self) -> None:
        page_source = (TASK_LIST_DIR / "KouboTaskListPage.jsx").read_text(encoding="utf-8")
        api_source = (TASK_LIST_DIR / "kouboTaskListApi.js").read_text(encoding="utf-8")
        modal_source = (TASK_LIST_DIR / "KouboTaskCreateDanceMimicModal.jsx").read_text(encoding="utf-8")

        self.assertIn("KouboTaskCreateDanceMimicModal", page_source)
        self.assertIn("createDanceMimic", page_source)
        self.assertIn("updateDanceMimic", api_source)
        self.assertIn("selectedDanceMimicTask", page_source)
        self.assertIn("saveDanceMimicTask", page_source)
        self.assertIn("listDanceMimicReferenceVideos", page_source)
        self.assertIn("listDanceMimicTargetImages", page_source)
        self.assertNotIn("onUploadReferenceVideo", page_source)
        self.assertNotIn("onUploadTargetImage", page_source)
        self.assertNotIn("后端 dance_mimic_v1 创建接口和运行页尚未接入", page_source)
        self.assertIn('request("/api/dance-mimic-v1/tasks/with-uploads"', api_source)
        self.assertIn('request(`/api/dance-mimic-v1/tasks/${taskId}/with-uploads`', api_source)
        self.assertIn("new FormData()", api_source)
        self.assertIn('request("/api/dance-mimic-v1/reference-videos"', api_source)
        self.assertIn('request("/api/dance-mimic-v1/target-images"', api_source)
        self.assertIn("reference_video_path", modal_source)
        self.assertIn("referenceVideos", modal_source)
        self.assertIn("参考舞蹈视频", modal_source)
        self.assertIn("拖拽视频到这里，或<strong>点击上传</strong>", modal_source)
        self.assertIn("referenceUploadFile", modal_source)
        self.assertIn('createSignal("upload")', modal_source)
        self.assertIn("libraryEnabled", modal_source)
        self.assertIn('disabled={!libraryEnabled()}', modal_source)
        self.assertIn("dropReferenceVideo", modal_source)
        self.assertIn("onListReferenceVideos", modal_source)
        self.assertNotIn("props.onUploadReferenceVideo", modal_source)
        self.assertIn("koubo-task-list-reference-picker", modal_source)
        self.assertIn("target_identity_image_path", modal_source)
        self.assertIn("目标人物图片", modal_source)
        self.assertIn("素材库", modal_source)
        self.assertIn("拖拽图片到这里，或<strong>点击上传</strong>", modal_source)
        self.assertIn("targetUploadFile", modal_source)
        self.assertIn("dropTargetImage", modal_source)
        self.assertIn("targetImages", modal_source)
        self.assertIn("preview_url", modal_source)
        self.assertIn("AI 生成人物", modal_source)
        self.assertIn("function canSave()", modal_source)
        self.assertIn("function canRun()", modal_source)
        self.assertIn("disabled={!canSave()}", modal_source)
        self.assertIn("disabled={!canRun()}", modal_source)
        self.assertIn("reference_privacy_mode", modal_source)
        self.assertIn("参考隐私模式", modal_source)
        self.assertIn('reference_privacy_mode: "face_mask_only"', modal_source)
        self.assertIn("provider_safe_outline", modal_source)
        self.assertIn("provider_safe_pose", modal_source)
        self.assertIn("参考人脸位置", modal_source)
        self.assertIn("face_detections_manifest", modal_source)
        self.assertIn("auto_run", modal_source)

    def test_create_mode_label_includes_dance_mimic(self) -> None:
        source = (TASK_LIST_DIR / "kouboTaskListStatus.js").read_text(encoding="utf-8")

        self.assertIn('dance_mimic: "动作模拟"', source)

    def test_create_menu_uses_chinese_action_mimic_label(self) -> None:
        source = (TASK_LIST_DIR / "KouboTaskCreateMenu.jsx").read_text(encoding="utf-8")
        page_source = (TASK_LIST_DIR / "KouboTaskListPage.jsx").read_text(encoding="utf-8")

        self.assertIn(">动作模拟</button>", source)
        self.assertNotIn(">DanceMimic</button>", source)
        self.assertLess(source.index(">视频分析</button>"), source.index(">人物口播</button>"))
        self.assertLess(source.index(">人物口播</button>"), source.index(">脚本生成</button>"))
        self.assertLess(source.index(">脚本生成</button>"), source.index(">动作模拟</button>"))
        self.assertNotIn('"刷新中..." : "刷新"', page_source)


if __name__ == "__main__":
    unittest.main()
