import json
import sys
from pathlib import Path


def config_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "configs"
    return Path(__file__).resolve().parent / "configs"


def last_config_path() -> Path:
    return config_dir() / "last_config.json"


def save_config_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_last_config(payload: dict) -> Path:
    path = last_config_path()
    save_config_file(path, payload)
    return path


def load_last_config() -> dict:
    return load_config_file(last_config_path())
