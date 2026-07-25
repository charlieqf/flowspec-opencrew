from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


class KouboTaskListOpenCodeAuthContract(unittest.TestCase):
    def test_opencode_auth_401_is_typed_and_recovered_for_prompt_builder(self) -> None:
        adapter_source = (REPO_ROOT / "backend" / "opcrew_backend" / "adapters" / "opencode.py").read_text(encoding="utf-8")
        runtime_source = (REPO_ROOT / "backend" / "opcrew_backend" / "services" / "opencode_runtime.py").read_text(encoding="utf-8")
        router_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py").read_text(encoding="utf-8")
        task_list_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "task_list_router.py").read_text(encoding="utf-8")
        step1_source = (REPO_ROOT / "backend" / "opcrew_backend" / "routes" / "step1_opencode.py").read_text(encoding="utf-8")

        self.assertIn("class OpenCodeAuthError", adapter_source)
        self.assertIn("if exc.code == 401", adapter_source)
        self.assertIn("raise OpenCodeAuthError", adapter_source)
        self.assertIn("def discover_and_save_opencode_runtime", runtime_source)
        self.assertIn("discover_and_save_opencode_runtime(ctx, reason=\"manual\")", step1_source)

        for token in (
            "except OpenCodeAuthError as exc:",
            "refresh_opencode_client_for(session_row, \"openclip.prompt_models.providers_401\")",
            "refresh_opencode_client_for(session_row, \"openclip.generate_prompt.prompt_async_401\")",
            "refresh_opencode_client_for(session_row, \"openclip.generate_prompt.messages_401\")",
        ):
            self.assertIn(token, router_source)

        self.assertIn("refresh_opencode_client_for(session_row, \"koubo_task.create_session_401\")", task_list_source)

    def test_task_list_api_extracts_fastapi_detail_messages(self) -> None:
        source = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboTaskList" / "kouboTaskListApi.js").read_text(encoding="utf-8")

        self.assertIn("function errorMessageFromResponseText", source)
        self.assertIn("const detail = payload?.detail", source)
        self.assertIn("return detail.trim()", source)
        self.assertIn("throw new Error(errorMessageFromResponseText", source)


if __name__ == "__main__":
    unittest.main()
