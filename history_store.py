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
    task_id = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    path = folder / f"task_{task_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    # 自动清理：只保留最近 100 条历史，避免长期使用无限增长（磁盘 + 列表加载变慢）
    try:
        stale = sorted(folder.glob("task_*.json"), reverse=True)
        for old in stale[100:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass
    return path


def list_history(max_items: int = 50) -> list[dict]:
    """返回最近历史（最多 max_items 条，按时间倒序），避免全量返回拖慢页面。"""
    folder = history_dir()
    if not folder.exists():
        return []
    items = []
    for path in sorted(folder.glob("task_*.json"), reverse=True):
        if len(items) >= max_items:
            break
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
