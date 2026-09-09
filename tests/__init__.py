"""Tests suite for `contact_center`."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent
_BY_PATH = {
    "contract": "contactcenter/app/knowledge_agent/contract.py",
    "retrieval": "contactcenter/app/knowledge_agent/retrieval.py",
    "bridge_handler": "infra/lambda/bridge/handler.py",
    "balance_handler": "infra/lambda/balance/handler.py",
}


def load_module(name: str) -> Any:
    """Load a module that lives outside the installed package, by file path.

    Parameters:
        name: Key in `_BY_PATH` (agent, bridge, or balance module).

    Returns:
        The freshly executed module.
    """
    spec = importlib.util.spec_from_file_location(name, _ROOT / _BY_PATH[name])
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
