"""Configuration loading: packaged defaults, deep-merged with an optional user YAML file.

String values may reference other keys with ``${section.key}``; references are resolved
after merging, so overriding ``paths.data_dir`` moves every derived path with it.
"""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

PKG_CONFIG_DIR = Path(__file__).parent / "config"
_REF = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _lookup(tree: dict, dotted: str) -> Any:
    node: Any = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def _resolve(tree: dict) -> dict:
    """Resolve ${a.b} references (iteratively, so chains like models_dir -> data_dir work)."""

    def resolve_value(val: Any, depth: int = 0) -> Any:
        if depth > 20:
            raise ValueError(f"reference cycle while resolving {val!r}")
        if isinstance(val, str):
            def sub(m: re.Match) -> str:
                return str(resolve_value(_lookup(tree, m.group(1)), depth + 1))
            return _REF.sub(sub, val)
        if isinstance(val, dict):
            return {k: resolve_value(v, depth) for k, v in val.items()}
        if isinstance(val, list):
            return [resolve_value(v, depth) for v in val]
        return val

    return resolve_value(tree)


class Config:
    """Read-only view over the merged configuration tree."""

    def __init__(self, tree: dict, languages: dict, source: str | None = None):
        self.tree = tree
        self.languages = languages
        self.source = source

    def get(self, dotted: str, default: Any = None) -> Any:
        try:
            return _lookup(self.tree, dotted)
        except KeyError:
            return default

    def __getitem__(self, dotted: str) -> Any:
        return _lookup(self.tree, dotted)

    def path(self, dotted: str) -> Path:
        return Path(os.path.expanduser(str(self[dotted])))

    def model_path(self, role: str) -> Path:
        return self.path("paths.models_dir") / self[f"models.{role}.file"]

    def language(self, code: str) -> dict:
        return self.languages.get(code) or {"name": code, "native": code, "iso3": code,
                                            "tts": "none", "font": "Noto Sans"}

    def ensure_dirs(self) -> None:
        for key in ("paths.jobs_dir", "paths.cache_dir"):
            self.path(key).mkdir(parents=True, exist_ok=True)


# Environment variables for container / cloud deployments (applied after the user YAML file).
ENV_OVERRIDES = {
    "GATEMOE_DATA_DIR": ("paths.data_dir", str),
    "GATEMOE_THREADS": ("hardware.threads", int),
    "GATEMOE_PORT": ("server.port", int),
    "GATEMOE_USER": ("server.auth.user", str),
    "GATEMOE_PASSWORD": ("server.auth.password", str),
    "GATEMOE_ALLOWED_HOSTS": ("server.allowed_hosts", lambda v: [h.strip() for h in v.split(",") if h.strip()]),
    "GATEMOE_MAX_PENDING_JOBS": ("server.max_pending_jobs", int),
}


def _env_overrides(environ) -> dict:
    tree: dict = {}
    for var, (dotted, conv) in ENV_OVERRIDES.items():
        raw = environ.get(var)
        if raw is None or raw == "":
            continue
        node = tree
        *parents, leaf = dotted.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = conv(raw)
    return tree


def load_config(path: str | os.PathLike | None = None, overrides: dict | None = None) -> Config:
    """Load packaged defaults, then the user file (arg or $GATEMOE_CONFIG), then overrides."""
    tree = yaml.safe_load((PKG_CONFIG_DIR / "default.yaml").read_text(encoding="utf-8"))
    languages = yaml.safe_load((PKG_CONFIG_DIR / "languages.yaml").read_text(encoding="utf-8"))
    source = None
    user_path = path or os.environ.get("GATEMOE_CONFIG")
    if user_path:
        user = yaml.safe_load(Path(user_path).read_text(encoding="utf-8")) or {}
        languages = _deep_merge(languages, user.pop("languages", {}) or {})
        tree = _deep_merge(tree, user)
        source = str(user_path)
    tree = _deep_merge(tree, _env_overrides(os.environ))
    if overrides:
        tree = _deep_merge(tree, overrides)
    return Config(_resolve(tree), languages, source)
