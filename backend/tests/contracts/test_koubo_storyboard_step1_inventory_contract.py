from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_SCRIPT = REPO_ROOT / "scripts" / "storyboard_step1_inventory.py"


class KouboStoryboardStep1InventoryContractTest(unittest.TestCase):
    def test_committed_inventories_match_current_tree(self) -> None:
        # Guards the two Step 1 Phase 0 inventories against drift:
        # - storyboard_context_injected_names.json feeds the AST migration gate;
        #   a stale snapshot silently weakens bare-name detection for every
        #   migrated module.
        # - storyboard_spawn_sites.json is the checklist of background-work
        #   spawn points that must switch to explicit `sc` passing in Phase S;
        #   new spawn sites must be reviewed and inventoried, not slip in.
        # Runs here (not the lint job) because rebuilding the context snapshot
        # imports backend app modules. Regenerate after intentional changes:
        #   backend/.venv/bin/python scripts/storyboard_step1_inventory.py --write
        result = subprocess.run(
            [sys.executable, str(INVENTORY_SCRIPT)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
