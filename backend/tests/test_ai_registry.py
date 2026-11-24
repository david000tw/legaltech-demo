"""子專案丙 Task 6：AI 來源登錄表（設計 5.12）。新增來源＝一個實作、一列登錄、一列單價。"""
import re
import typing
from pathlib import Path

from app.ai.registry import REGISTRY
from app.config import Settings

APP = Path(__file__).resolve().parent.parent / "app"
VENDOR_IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:subprocess|app\.ai\.claude_code|app\.ai\.bedrock)\b", re.M)


def test_registry_matches_settings():
    allowed = set(typing.get_args(Settings.model_fields["ai_provider"].annotation))
    assert set(REGISTRY) == allowed
    assert REGISTRY["claude_code_local"].billing == "unmetered" and REGISTRY["claude_code_local"].needs_key is False
    assert REGISTRY["bedrock"].billing == "metered" and REGISTRY["bedrock"].needs_key is True
    assert all(info.name == key and info.label for key, info in REGISTRY.items())


def test_no_direct_vendor_calls_outside_ai_package():
    offenders = [str(p.relative_to(APP)) for p in APP.rglob("*.py")
                 if "ai" not in p.relative_to(APP).parts[:1] and VENDOR_IMPORT.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
