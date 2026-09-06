import json
import sys
import time
from pathlib import Path


def history_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "history"
    return Path(__file__).resolve().parent / "history"


def save_history(record: dict) -> Path:
    folder = history_dir()
    folder.mkdir(parents=True, exist_ok=True)
    task_id = time.strftime("%Y%m%d_%H%M%S")
    path = folder / f"task_{task_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def list_history() -> list[dict]:
    folder = history_dir()
    if not folder.exists():
        return []
    items = []
    for path in sorted(folder.glob("task_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["_file"] = path.name
                items.append(data)
        except Exception:
            continue
    return items

def clear_history() -> int:
    folder = history_dir()
    if not folder.exists():
        return 0
    files = list(folder.glob("task_*.json"))
    count = 0
    for path in files:
        try:
            path.unlink(missing_ok=True)
            count += 1
        except Exception:
            continue
    return count
