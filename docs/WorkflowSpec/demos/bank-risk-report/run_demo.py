from pathlib import Path
import sys

DEMO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO_ROOT))

from demo_runtime import build_scenario  # noqa: E402
from generate_html import generate_scenario_page  # noqa: E402


if __name__ == "__main__":
    scenario = Path(__file__).resolve().parent
    build_scenario(scenario)
    generate_scenario_page(scenario)
