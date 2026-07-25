from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_rewrite_module():
    path = REPO_ROOT / "ToolLibrary" / "Analysis_V1" / "04_01_SRTRewrite.py"
    spec = importlib.util.spec_from_file_location("analysis_v1_04_01_rewrite_resume_contract", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalysisV1SrtRewriteResumeContractTest(unittest.TestCase):
    def test_original_dialogue_passthrough_copies_original_srt_without_model_config(self) -> None:
        module = load_rewrite_module()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionOutput" / "subtitle").mkdir(parents=True)
            variables = {
                "rewrite_prompt": {
                    "final_prompt": "请还原我的原视频脚本，不需要重新改写。改写 SRT 要和原 SRT 一样。",
                    "source": "contract_test",
                },
                "product_info": "还原我的原视频脚本",
            }
            final_items = {
                "items": [
                    {
                        "srt_id": "srt_0001",
                        "dialogue": "第一句原视频口播。",
                        "image_path": "SessionOutput/frames/001.jpg",
                        "start": 0,
                        "end": 1.2,
                        "duration": 1.2,
                    },
                    {
                        "srt_id": "srt_0002",
                        "dialogue": "第二句也要保持原样。",
                        "image_path": "SessionOutput/frames/002.jpg",
                        "start": 1.2,
                        "end": 2.5,
                        "duration": 1.3,
                    },
                ]
            }
            (workspace / "SessionContext" / "Variables.json").write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")
            (workspace / "SessionOutput" / "subtitle" / "final_srt_frame_items.json").write_text(json.dumps(final_items, ensure_ascii=False), encoding="utf-8")

            result = module.run(module.Args(
                workspace=str(workspace),
                model_provider="",
                model_id="",
                database_url="",
                database_url_env="OPENCREW_DATABASE_URL",
                force=False,
                resume=False,
                force_regenerate_prompts=False,
                max_repair_attempts=0,
                print_json=True,
            ))

            output = json.loads((workspace / "SessionOutput" / "subtitle" / "rewritten_srt_items.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["requires_model_calls"])
            self.assertTrue(output["passthrough_original_dialogue"])
            self.assertEqual(output["identity_policy"], module.ORIGINAL_DIALOGUE_PASSTHROUGH_POLICY)
            self.assertEqual([item["dialogue"] for item in output["items"]], [item["dialogue"] for item in final_items["items"]])
            self.assertEqual([item["original_dialogue"] for item in output["items"]], [item["dialogue"] for item in final_items["items"]])
            self.assertFalse((workspace / "S6_04_01_SRTRewrite" / "Prompt" / "00_srt_rewrite_prompt.md").exists())
            self.assertIn("第一句原视频口播。", (workspace / "SessionOutput" / "subtitle" / "rewritten_dialogue.srt").read_text(encoding="utf-8"))

    def test_rewrite_prompt_preserving_metadata_is_not_original_dialogue_passthrough(self) -> None:
        module = load_rewrite_module()
        variables = {
            "rewrite_prompt": {
                "final_prompt": "\n".join([
                    "请基于以下业务参数进行改写：",
                    "产品信息：化橘红",
                    "结合行业、人设、目标受众和产品信息，沿用原对白的句序、节奏和表达功能，逐句生成新对白。",
                    "将原对白中的产品、卖点、表达重心，替换为“化橘红”相关内容。",
                    "必须保持 srt_id 不变、句子顺序不变、时间轴不变、图片帧不变。",
                    "改写后的对白必须使用简体中文，禁止使用繁体字；英文、数字、品牌名保持原样。",
                ]),
                "source": "contract_test",
            },
            "product_info": "化橘红",
        }

        self.assertFalse(module.wants_original_dialogue_passthrough(variables))

    def test_rewrite_content_preserving_srt_structure_is_not_original_dialogue_passthrough(self) -> None:
        module = load_rewrite_module()
        variables = {
            "rewrite_prompt": {
                "final_prompt": "\n".join([
                    "你将执行一次 SRT 口播对白逐句改写任务。",
                    "请基于已识别出的原始 SRT 口播对白，结合业务参数，逐句生成新的口播对白。",
                    "只替换和重写对白内容，其他 SRT 结构信息必须保持原样。",
                    "必须保持每条字幕的 srt_id 不变、顺序不变、时间轴不变、图片帧不变。",
                    "最终只输出与原 SRT 一一对应的新对白内容，并保留原有 SRT 对应结构。",
                ]),
                "source": "contract_test",
            },
            "product_info": "中钥化橘红",
        }

        self.assertFalse(module.wants_original_dialogue_passthrough(variables))

    def test_rewrite_text_preserving_number_time_and_frame_is_not_original_dialogue_passthrough(self) -> None:
        module = load_rewrite_module()
        variables = {
            "rewrite_prompt": {
                "final_prompt": "\n".join([
                    "你将执行一项 SRT 逐句改写任务。",
                    "请基于已识别出的原始口播对白，结合业务参数，逐句生成新的口播对白。",
                    "将原对白中的产品、卖点、痛点、利益点和表达重心，替换为“中钥化橘红”的产品信息与品牌定位。",
                    "必须保持每条字幕的 srt_id 不变、顺序不变、时间轴不变、图片帧不变。",
                    "只改写对白文本，不改变任何 SRT 编号、时间码、字幕顺序或画面对应关系。",
                ]),
                "source": "contract_test",
            },
            "product_info": "中钥化橘红",
        }

        self.assertFalse(module.wants_original_dialogue_passthrough(variables))

    def test_rewrite_text_with_short_metadata_nochange_is_not_original_dialogue_passthrough(self) -> None:
        module = load_rewrite_module()
        variables = {
            "rewrite_prompt": {
                "final_prompt": "\n".join([
                    "请基于原始 SRT 口播对白逐句改写。",
                    "只改写对白文本，不改任何 SRT 编号、时间码、字幕顺序或画面对应关系。",
                    "时间码一个字都不要改，每个输入句子只输出一个对应新句子。",
                ]),
                "source": "contract_test",
            },
            "product_info": "中钥化橘红",
        }

        self.assertFalse(module.wants_original_dialogue_passthrough(variables))

    def test_explicit_dialogue_preservation_still_requests_original_dialogue_passthrough(self) -> None:
        module = load_rewrite_module()
        variables = {
            "rewrite_prompt": {
                "final_prompt": "不要改写对白内容，保持原样，直接使用原 SRT。",
                "source": "contract_test",
            },
            "product_info": "中钥化橘红",
        }

        self.assertTrue(module.wants_original_dialogue_passthrough(variables))

    def test_create_from_script_product_or_constraints_script_phrase_requests_passthrough(self) -> None:
        module = load_rewrite_module()
        base_variables = {
            "rewrite_prompt": {
                "final_prompt": "普通脚本生成提示词。",
                "source": "contract_test",
            },
            "product_info": "",
            "constraints": "",
        }

        phrases = [
            "按照我的脚本进行，不要修改我的脚本",
            "按照我的脚本进行，不要改写我的脚本",
            "不要改写我的脚本",
            "脚本别动，一个字都不要改",
            "按我给的文案一字不改",
            "台词别改，照着原文来",
            "口播内容不要动，直接使用",
            "严格按照原脚本进行",
            "不要改原脚本，按原脚本一句一句生成",
        ]
        for field in ("product_info", "constraints"):
            for phrase in phrases:
                with self.subTest(field=field, phrase=phrase):
                    variables = {
                        **base_variables,
                        field: phrase,
                    }
                    self.assertTrue(module.wants_original_dialogue_passthrough(variables))

    def test_create_from_script_simple_prompt_constraints_phrase_requests_passthrough(self) -> None:
        module = load_rewrite_module()
        variables = {
            "rewrite_prompt": {
                "simple_prompt": "\n".join([
                    "请基于以下业务参数，生成一份用于 SRT 逐句改写任务的最终提示词。",
                    "约束条件：按照我的脚本进行，不要改写我的脚本",
                    "最终提示词要清晰、完整、可直接交给后续改写模型执行。",
                ]),
                "final_prompt": "\n".join([
                    "请基于用户给定脚本执行 SRT Rewrite。",
                    "如需优化表达，只围绕行业、人设、目标受众、产品信息和约束条件做轻量改写。",
                    "约束条件：-",
                ]),
                "source": "openclip_tasks.rewrite_final_prompt",
            },
            "rewrite_final_prompt": "约束条件：-",
            "product_info": "",
            "constraints": "",
        }

        self.assertTrue(module.wants_original_dialogue_passthrough(variables))

    def test_structure_only_preservation_does_not_request_original_dialogue_passthrough(self) -> None:
        module = load_rewrite_module()
        variables = {
            "rewrite_prompt": {
                "final_prompt": "\n".join([
                    "请改写我的脚本，让表达更适合大健康受众。",
                    "脚本结构不要改，必须保持 srt_id、时间轴、图片帧不变。",
                    "每个输入句子只输出一个对应新句子。",
                ]),
                "source": "contract_test",
            },
            "product_info": "助眠课程",
        }

        self.assertFalse(module.wants_original_dialogue_passthrough(variables))

    def test_simple_prompt_constraints_passthrough_copies_original_srt_without_model_config(self) -> None:
        module = load_rewrite_module()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "SessionContext").mkdir(parents=True)
            (workspace / "SessionOutput" / "subtitle").mkdir(parents=True)
            variables = {
                "rewrite_prompt": {
                    "simple_prompt": "\n".join([
                        "请基于以下业务参数，生成一份用于 SRT 逐句改写任务的最终提示词。",
                        "约束条件：按照我的脚本进行，不要改写我的脚本",
                        "最终提示词要清晰、完整、可直接交给后续改写模型执行。",
                    ]),
                    "final_prompt": "约束条件：-",
                    "source": "openclip_tasks.rewrite_final_prompt",
                },
                "rewrite_final_prompt": "约束条件：-",
            }
            final_items = {
                "items": [
                    {
                        "srt_id": "srt_0001",
                        "dialogue": "不要被改写的原脚本。",
                        "image_path": "",
                        "start": 0,
                        "end": 1,
                        "duration": 1,
                    },
                ]
            }
            (workspace / "SessionContext" / "Variables.json").write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")
            (workspace / "SessionOutput" / "subtitle" / "final_srt_frame_items.json").write_text(json.dumps(final_items, ensure_ascii=False), encoding="utf-8")

            result = module.run(module.Args(
                workspace=str(workspace),
                model_provider="",
                model_id="",
                database_url="",
                database_url_env="OPENCREW_DATABASE_URL",
                force=False,
                resume=False,
                force_regenerate_prompts=False,
                max_repair_attempts=0,
                print_json=True,
            ))

            output = json.loads((workspace / "SessionOutput" / "subtitle" / "rewritten_srt_items.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["requires_model_calls"])
            self.assertTrue(output["passthrough_original_dialogue"])
            self.assertEqual(output["items"][0]["dialogue"], "不要被改写的原脚本。")
            self.assertFalse((workspace / "S6_04_01_SRTRewrite" / "Prompt" / "00_srt_rewrite_prompt.md").exists())

    def test_resume_reuses_output_only_when_prompt_and_business_context_match(self) -> None:
        module = load_rewrite_module()
        variables = {
            "rewrite_prompt": {
                "final_prompt": "Rewrite for product A.",
                "source": "openclip_tasks.rewrite_final_prompt",
            },
            "product_info": "Product A",
            "constraints": "Keep concise.",
        }
        existing = {
            "rewrite_final_prompt_sha256": hashlib.sha256(b"Rewrite for product A.").hexdigest(),
            "prompt_source": "openclip_tasks.rewrite_final_prompt",
            "business_context": module.business_context(variables),
            "items": [],
        }

        self.assertEqual(module.reusable_rewrite_output(existing, variables), (True, ""))

        prompt_changed = {
            **variables,
            "rewrite_prompt": {
                "final_prompt": "Rewrite for product B.",
                "source": "openclip_tasks.rewrite_final_prompt",
            },
        }
        self.assertEqual(module.reusable_rewrite_output(existing, prompt_changed), (False, "rewrite_prompt_changed"))

        context_changed = {**variables, "product_info": "Product B"}
        self.assertEqual(module.reusable_rewrite_output(existing, context_changed), (False, "business_context_changed"))
        self.assertEqual(module.reusable_rewrite_output([], variables), (False, "existing_rewrite_output_invalid"))

    def test_prompt_save_syncs_existing_variables_snapshot(self) -> None:
        router_source = (REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py").read_text(encoding="utf-8")

        self.assertIn('workspace_dir = str(task_row.get("workspace_dir") or "").strip()', router_source)
        self.assertIn("if workspace_dir:", router_source)
        self.assertIn("def sync_analysis_v1_variables_prompt_snapshot", router_source)
        self.assertIn('"SessionContext" / "Variables.json"', router_source)
        self.assertIn('"product_info"', router_source)
        self.assertIn('"rewrite_prompt"', router_source)
        self.assertIn("variables_synced = sync_analysis_v1_variables_prompt_snapshot(get_task(task_id))", router_source)
        self.assertIn('"analysis_v1_variables_synced": variables_synced', router_source)

    def test_progress_primary_button_reruns_existing_attempt_from_scratch(self) -> None:
        source = (REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "AnalysisV1Module.jsx").read_text(encoding="utf-8")

        self.assertIn("function previousRunAttemptId()", source)
        self.assertIn("function shouldRerunEntireTask()", source)
        self.assertIn('return { mode: "rerun_all", previous_attempt_id: previousRunAttemptId() };', source)
        self.assertIn("function primaryRunActionLabel()", source)
        self.assertIn('"重跑整个任务"', source)
        self.assertIn("title={primaryRunActionLabel()} aria-label={primaryRunActionLabel()}", source)
        self.assertIn("onClick={() => void runAnalysis(defaultRunOverrides())}", source)
        self.assertIn('const mode = String(overrides.mode || runMode() || "run_all");', source)
        self.assertNotIn('if (!isAdmin()) {\n      return {\n        mode: "run_all"', source)
        self.assertEqual(source.count("payload.previous_attempt_id = overrides.previous_attempt_id"), 1)


if __name__ == "__main__":
    unittest.main()
