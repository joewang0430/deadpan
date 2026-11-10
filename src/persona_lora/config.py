"""Load YAML configs (with one level of `inherit`) and resolve project paths."""
from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "pipeline.yaml"


def _merge(base: dict, override: dict) -> dict:
    """Two-level merge: top-level sections merge key by key, and each key's value
    is replaced wholesale. So `roles.teacher` in an override replaces the whole
    teacher spec instead of mixing old and new fields."""
    merged = dict(base)
    for section, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(section), dict):
            merged[section] = {**merged[section], **value}
        else:
            merged[section] = value
    return merged


def load_config(path: str | Path | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.is_absolute():
        path = ROOT / path
    with path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    parent = cfg.pop("inherit", None)
    if parent:
        cfg = _merge(load_config(path.parent / parent), cfg)
    return cfg


def data_dir(cfg: dict) -> Path:
    return ROOT / cfg["paths"]["data_dir"]


def gen_dir(cfg: dict) -> Path:
    return ROOT / cfg["paths"]["gen_dir"]


def read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8").strip()


def persona_prompt() -> str:
    return read_text("persona/system_prompt.md")
