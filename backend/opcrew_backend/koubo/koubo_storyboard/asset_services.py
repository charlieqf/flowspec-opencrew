from __future__ import annotations

from typing import Any

from .asset_core_services import register_asset_core_services
from .working_asset_services import register_working_asset_services
from .asset_reference_services import register_asset_reference_services
from .asset_history_services import register_asset_history_services
from .asset_pool_services import register_asset_pool_services
from .working_reset_services import register_working_reset_services


def register_asset_services(ns: Any) -> None:
    register_asset_core_services(ns)
    register_working_asset_services(ns)
    register_asset_reference_services(ns)
    register_asset_history_services(ns)
    register_asset_pool_services(ns)
    register_working_reset_services(ns)
