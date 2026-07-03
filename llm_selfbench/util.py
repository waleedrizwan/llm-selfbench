from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: os.PathLike[str] | str) -> Dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: os.PathLike[str] | str, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")


def append_jsonl(path: os.PathLike[str] | str, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=False)
        f.write("\n")


def slugify(value: str, max_len: int = 90) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_.-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return (value[:max_len] or "item")


def render_template(template: str, data: Mapping[str, Any]) -> str:
    """Very small {{name}} renderer, deliberately dependency-free."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        cur: Any = data
        for part in key.split("."):
            if isinstance(cur, Mapping) and part in cur:
                cur = cur[part]
            else:
                return match.group(0)
        if isinstance(cur, (dict, list)):
            return json.dumps(cur, ensure_ascii=False)
        return str(cur)

    return re.sub(r"{{\s*([a-zA-Z0-9_.-]+)\s*}}", replace, template)


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def env_with(extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    env = os.environ.copy()
    if extra:
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[str(k)] = str(v)
    return env


def percent(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{100 * value:.1f}%"


def mean(values: Iterable[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)
