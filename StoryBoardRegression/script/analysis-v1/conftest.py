from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    path = Path(__file__).parent / "cassettes"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


@pytest.fixture(scope="module")
def vcr_config(vcr_cassette_dir: str) -> dict:
    return {"cassette_library_dir": vcr_cassette_dir}
