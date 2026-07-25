from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TIMING_MENU_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "components" / "KouboTimingMenu.jsx"
TTS_CONTROLLER_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardTts.js"
TIMING_MENU_CSS_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "styles" / "timing-menu.css"
TOOLBAR_CSS_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "styles" / "toolbar.css"


class KouboStoryboardTTSTemplateContractTest(unittest.TestCase):
    def test_timing_menu_exposes_save_gated_template_selector(self) -> None:
        source = TIMING_MENU_PATH.read_text(encoding="utf-8")
        timing_css = TIMING_MENU_CSS_PATH.read_text(encoding="utf-8")
        toolbar_css = TOOLBAR_CSS_PATH.read_text(encoding="utf-8")

        self.assertIn("TTS_TEMPLATE_OPTIONS", source)
        self.assertIn('id: "single-basic"', source)
        self.assertIn('id: "short-video-natural"', source)
        self.assertIn('id: "steady-explainer"', source)
        self.assertIn('id: "expressive-tags"', source)
        self.assertIn("TTS Template", source)
        self.assertIn('value={selectedTemplateId()}', source)
        self.assertIn("updateTemplate", source)
        self.assertIn("promptTextareaEl.value = prompt", source)
        self.assertIn("scenarioId,", source)
        self.assertIn("scenario_id: scenarioId", source)
        self.assertIn("await props.saveAudioSettings?.(next)", source)
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr))", timing_css)
        self.assertIn("width: min(680px, calc(100vw - 48px))", toolbar_css)

    def test_audio_settings_persist_template_id_and_prompt_for_storyboard_tts(self) -> None:
        source = TTS_CONTROLLER_PATH.read_text(encoding="utf-8")

        self.assertIn("scenarioId: selection?.scenario_id", source)
        self.assertIn("values?.scenarioId ?? values?.scenario_id", source)
        self.assertIn("prompt_template: prompt || selectedCandidate?.prompt_template", source)
        self.assertIn("scenario_id: scenarioId", source)
        self.assertIn('source: "storyboard_audio_settings"', source)
        self.assertIn("prompt: applyTtsTextToPrompt(card.prompt, card.provider, text)", source)

    def test_locked_tts_cache_accepts_provider_prompts_without_embedded_body_text(self) -> None:
        source = TTS_CONTROLLER_PATH.read_text(encoding="utf-8")

        self.assertIn("const promptText = ttsPromptBodyText(manifest?.prompt || config?.prompt)", source)
        self.assertIn("return !promptText || promptText === expected", source)


if __name__ == "__main__":
    unittest.main()
