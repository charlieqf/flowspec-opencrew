from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "frontend/src/modules/koubo/KouboStoryBoardModule.jsx"


class KouboStoryboardScrollAnchorContractTest(unittest.TestCase):
    def test_structural_edits_preserve_viewport_and_new_dialogue_receives_focus(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn('const previousScrollTop = scrollContainer?.scrollTop || 0;', source)
        self.assertIn('scrollContainer.scrollTop = previousScrollTop;', source)
        self.assertIn('function focusDialogueEditor(dialogueId)', source)
        self.assertIn('requestAnimationFrame(() => requestAnimationFrame(focus));', source)
        self.assertIn('if (nextDialogueId) focusDialogueEditor(nextDialogueId);', source)


if __name__ == "__main__":
    unittest.main()
