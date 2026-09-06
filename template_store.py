import json
import sys
from pathlib import Path


def template_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "templates"
    return Path(__file__).resolve().parent / "templates"


def _safe_name(name: str) -> str:
    return Path(name).name.replace(" ", "_")


def save_template(name: str, payload: dict) -> Path:
    folder = template_dir()
    folder.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(name)
    if not safe:
        raise ValueError("模板名称无效")
    path = folder / f"{safe}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_template(name: str) -> dict:
    path = template_dir() / f"{_safe_name(name)}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def list_templates() -> list[str]:
    folder = template_dir()
    if not folder.exists():
        return []
    return sorted([p.stem for p in folder.glob("*.json")])


def delete_template(name: str) -> bool:
    path = template_dir() / f"{_safe_name(name)}.json"
    if path.exists():
        path.unlink(missing_ok=True)
        return True
    return False
