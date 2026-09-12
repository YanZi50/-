# 便携版隔离：打包运行时剔除外部 PYTHONPATH 注入（如豆包 python-packages），
# 确保模块一律从 _internal 打包副本加载，避免引用开发机路径导致换机不可用。
import sys as _sys

if getattr(_sys, "frozen", False):
    _ppath = [p for p in _sys.path if p and "python-packages" in p]
    for _p in _ppath:
        _sys.path.remove(_p)

import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from config_store import load_last_config, save_last_config
from history_store import clear_history, list_history, save_history
from template_store import delete_template, list_templates, load_template, save_template
from platform_presets import apply_preset, get_presets
import subtitle_plugin

from video_engine import (
    BatchResult,
    JobConfig,
    MediaError,
    get_thumbnail,
    precheck_materials,
    process_batch,
    process_failed_items,
    probe_media,
    scan_audio,
    scan_videos,
)


HOST = "127.0.0.1"
PORT = 8765
if getattr(sys, "frozen", False):
    WEB_DIR = Path(sys._MEIPASS) / "web"
    UPLOAD_ROOT = Path(sys.executable).resolve().parent / "uploads"
    PREVIEW_DIR = Path(sys.executable).resolve().parent / "previews"
else:
    WEB_DIR = Path(__file__).parent / "web"
    UPLOAD_ROOT = Path(__file__).parent / "uploads"
    PREVIEW_DIR = Path(__file__).parent / "previews"


# ---------------------------------------------------------------------------
# 下载白名单：download / zip 只允许访问被登记的输出目录
# ---------------------------------------------------------------------------
ALLOWED_DIRS: set[str] = set()
ALLOWED_LOCK = threading.Lock()


def register_allowed_dir(path: str) -> None:
    if not path:
        return
    real = os.path.realpath(path)
    with ALLOWED_LOCK:
        ALLOWED_DIRS.add(real)


def is_allowed_dir(path: str) -> bool:
    real = os.path.realpath(path)
    with ALLOWED_LOCK:
        for base in ALLOWED_DIRS:
            if real == base or real.startswith(base + os.sep):
                return True
    return False


def register_standard_dirs() -> None:
    register_allowed_dir(str(PREVIEW_DIR))
    register_allowed_dir(str(UPLOAD_ROOT))


# ---------------------------------------------------------------------------
# 应用状态
# ---------------------------------------------------------------------------
class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.paused = False
        self.logs: list[str] = []
        self.current = 0
        self.total = 0
        self.result: BatchResult | None = None
        self.error: str | None = None
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_config: JobConfig | None = None
        self.last_failed_items: list[dict] = []
        self.started_at: float | None = None
        self.samples: list[tuple[float, int]] = []

    def add_log(self, message: str) -> None:
        with self.lock:
            self.logs.append(message)
            self.logs = self.logs[-1000:]

    def set_progress(self, current: int, total: int) -> None:
        with self.lock:
            self.current = current
            self.total = total
            if self.started_at is not None:
                self.samples.append((time.time(), current))
                self.samples = self.samples[-12:]

    def begin(self, total: int) -> None:
        with self.lock:
            self.started_at = time.time()
            self.samples = [(time.time(), 0)]
            self.current = 0
            self.total = total

    def end(self) -> None:
        with self.lock:
            self.started_at = None
            self.samples = []

    def _compute_rate(self, samples: list[tuple[float, int]]) -> float | None:
        if len(samples) < 2:
            return None
        ts0, done0 = samples[0]
        ts1, done1 = samples[-1]
        dt = ts1 - ts0
        if dt < 0.5 or done1 < done0:
            return None
        return (done1 - done0) / dt

    def eta_seconds(self) -> float | None:
        with self.lock:
            if self.started_at is None or self.total <= 0 or self.current >= self.total:
                return None
            samples = list(self.samples)
            current = self.current
            total = self.total
        rate = self._compute_rate(samples)
        if not rate or rate <= 0:
            return None
        return (total - current) / rate

    def speed_per_sec(self) -> float | None:
        with self.lock:
            if self.started_at is None:
                return None
            samples = list(self.samples)
        return self._compute_rate(samples)

    def status_dict(self) -> dict:
        with self.lock:
            data = {
                "running": self.running,
                "paused": self.paused,
                "current": self.current,
                "total": self.total,
                "logs": list(self.logs),
                "success": self.result.success if self.result else 0,
                "skipped": self.result.skipped if self.result else 0,
                "failed": self.result.failed if self.result else 0,
                "cancelled": self.result.cancelled if self.result else False,
                "failed_items": self.result.failed_items if self.result else [],
                "success_items": self.result.success_items if self.result else [],
                "error": self.error,
            }
            samples = list(self.samples)
            started_at = self.started_at
            current = self.current
            total = self.total
        data["eta_seconds"] = None
        data["speed_per_sec"] = None
        if started_at is not None and total > 0 and current < total:
            rate = self._compute_rate(samples)
            if rate and rate > 0:
                data["speed_per_sec"] = rate
                data["eta_seconds"] = (total - current) / rate
        return data


def run_folder_dialog(description: str) -> str:
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.WindowState = 'Minimized'
$owner.Show()
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = '{description}'
$d.ShowNewFolderButton = $true
$result = $d.ShowDialog($owner)
$owner.Close()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{ $d.SelectedPath }}
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        return (proc.stdout or "").strip()
    except Exception:
        return ""


STATE = AppState()
SELECT_LOCK = threading.Lock()


def _safe_float(value, default: float, lo: float | None = None, hi: float | None = None) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _safe_int(value, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        v = int(value)
    except Exception:
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: ANN001
        return

    # ----------------------------------------------------------------
    # 基础工具
    # ----------------------------------------------------------------
    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, mime: str, as_attachment: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "not found")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        if as_attachment:
            ascii_name = as_attachment.encode("ascii", "ignore").decode() or "download"
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{ascii_name}"',
            )
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ----------------------------------------------------------------
    # GET 路由
    # ----------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/":
            self._serve_file("index.html")
            return
        if route == "/style.css":
            self._serve_file("style.css", "text/css; charset=utf-8")
            return
        if route == "/app.js":
            self._serve_file("app.js", "application/javascript; charset=utf-8")
            return
        if route == "/vendor/vue.global.prod.js":
            self._serve_file("vendor/vue.global.prod.js", "application/javascript; charset=utf-8")
            return
        if route == "/api/platform_presets":
            self._send_json({"presets": get_presets()})
            return
        if route == "/api/platform_presets/apply":
            name = (query.get("name") or [""])[0]
            self._send_json(apply_preset(name))
            return
        if route == "/api/templates":
            self._send_json({"templates": list_templates()})
            return
        if route == "/api/history/clear":
            self._send_json({"ok": True, "removed": clear_history()})
            return
        if route == "/api/history":
            self._send_json({"history": list_history()})
            return
        if route == "/api/config/load":
            self._send_json(load_last_config())
            return
        if route == "/api/status":
            self._send_json(STATE.status_dict())
            return
        if route == "/api/debug":
            import video_engine
            self._send_json({
                "frozen": getattr(sys, "frozen", False),
                "executable": sys.executable,
                "meipass": getattr(sys, "_MEIPASS", None),
                "web_dir": str(WEB_DIR),
                "engine_file": video_engine.__file__,
                "app_root": str(video_engine._app_root()),
                "cache_dir": str(video_engine.cache_dir()),
                "ffmpeg": video_engine._ffmpeg(),
            })
            return
        if route == "/api/scan":
            head = (query.get("head") or [""])[0]
            tail = (query.get("tail") or [""])[0]
            middle = (query.get("middle") or [""])[0]
            bgm = (query.get("bgm") or [""])[0]
            head_files = scan_videos(head)
            tail_files = scan_videos(tail)
            middle_files = scan_videos(middle)
            bgm_files = scan_audio(bgm)
            self._send_json(
                {
                    "head": [{"name": Path(p).name, "path": p} for p in head_files],
                    "tail": [{"name": Path(p).name, "path": p} for p in tail_files],
                    "middle": [{"name": Path(p).name, "path": p} for p in middle_files],
                    "bgm": [{"name": Path(p).name, "path": p} for p in bgm_files],
                }
            )
            return
        if route == "/api/material_detail":
            path = (query.get("path") or [""])[0]
            if not path or not os.path.isfile(path):
                self._send_json({"ok": False, "error": "文件不存在"}, 404)
                return
            info = probe_media(path)
            self._send_json({"ok": True, "path": path, **info})
            return
        if route == "/api/thumb":
            path = (query.get("path") or [""])[0]
            thumb = get_thumbnail(path) if path else None
            if not thumb:
                self.send_error(404, "no thumbnail")
                return
            self._send_file(Path(thumb), "image/jpeg")
            return
        if route == "/api/select_folder":
            name = (query.get("name") or ["head"])[0]
            desc = {
                "head": "选择开头素材文件夹",
                "tail": "选择结尾素材文件夹",
                "middle": "选择中间素材文件夹",
                "output": "选择输出路径",
                "bgm": "选择音乐文件夹",
            }.get(name, "选择文件夹")
            if not SELECT_LOCK.acquire(blocking=False):
                self._send_json({"busy": True, "path": ""})
                return
            try:
                path = run_folder_dialog(desc)
                self._send_json({"path": path, "busy": False})
            finally:
                SELECT_LOCK.release()
            return
        if route == "/api/upload_path":
            kind = (query.get("kind") or ["head"])[0]
            path = self._upload_folder_for_kind(kind)
            self._send_json({"path": str(path)})
            return
        if route == "/api/list_output":
            folder = (query.get("folder") or [""])[0]
            self._list_output(folder)
            return
        if route == "/api/download":
            folder = (query.get("folder") or [""])[0]
            name = (query.get("name") or [""])[0]
            self._download_file(folder, name)
            return
        self._send_json({"error": "not found"}, 404)

    # ----------------------------------------------------------------
    # POST 路由
    # ----------------------------------------------------------------
    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/api/templates/save":
            payload = self._read_json()
            name = str(payload.get("name", "")).strip()
            if not name:
                self._send_json({"ok": False, "error": "模板名称不能为空"}, 400)
                return
            try:
                path = save_template(name, payload.get("config", {}))
                self._send_json({"ok": True, "path": str(path)})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        if route == "/api/templates/load":
            payload = self._read_json()
            name = str(payload.get("name", "")).strip()
            self._send_json(load_template(name))
            return
        if route == "/api/templates/delete":
            payload = self._read_json()
            name = str(payload.get("name", "")).strip()
            self._send_json({"ok": delete_template(name)})
            return
        if route == "/api/config/save":
            payload = self._read_json()
            path = save_last_config(payload)
            self._send_json({"ok": True, "path": str(path)})
            return
        if route == "/api/precheck":
            payload = self._read_json()
            config = self._make_config(payload)
            if isinstance(config, str):
                self._send_json({"ok": False, "error": config}, 400)
                return
            report = precheck_materials(config)
            bad = [
                {"kind": kind, "name": item["name"], "error": item["error"]}
                for kind, items in report.items()
                for item in items
                if not item["ok"]
            ]
            self._send_json({"ok": True, "report": report, "bad": bad})
            return
        if route == "/api/zip":
            payload = self._read_json()
            folder = str(payload.get("folder", "")).strip()
            self._zip_output(folder)
            return
        if route == "/api/preview":
            self._preview()
            return
        if route == "/api/start":
            self._start()
            return
        if route == "/api/retry_failed":
            self._retry_failed()
            return
        if route == "/api/cancel":
            STATE.cancel_event.set()
            STATE.add_log("已请求取消")
            self._send_json({"ok": True})
            return
        if route == "/api/pause":
            self._toggle_pause()
            self._send_json({"paused": STATE.paused})
            return
        if route == "/api/upload":
            self._upload_file(parsed)
            return
        self._send_json({"error": "not found"}, 404)

    # ----------------------------------------------------------------
    # 素材与文件
    # ----------------------------------------------------------------
    def _upload_folder_for_kind(self, kind: str) -> Path:
        if kind == "head":
            folder = UPLOAD_ROOT / "head"
        elif kind == "tail":
            folder = UPLOAD_ROOT / "tail"
        elif kind == "middle":
            folder = UPLOAD_ROOT / "middle"
        elif kind in {"watermark", "bgm"}:
            folder = UPLOAD_ROOT / "files"
        elif re.fullmatch(r"middle_pool[1-5]", kind):
            folder = UPLOAD_ROOT / kind
        else:
            folder = UPLOAD_ROOT / "files"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _upload_file(self, parsed) -> None:
        query = parse_qs(parsed.query)
        kind = (query.get("kind") or ["files"])[0]
        raw_name = (query.get("name") or ["file"])[0]
        safe_name = Path(raw_name).name
        if not safe_name:
            self._send_json({"ok": False, "error": "文件名无效"}, 400)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            self._send_json({"ok": False, "error": "没有收到文件内容"}, 400)
            return
        folder = self._upload_folder_for_kind(kind)
        dest = folder / safe_name
        with dest.open("wb") as f:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        self._send_json({"ok": True, "path": str(dest)})

    def _list_output(self, folder: str) -> None:
        path = Path(folder)
        if not path.exists() or not path.is_dir():
            self._send_json({"files": []})
            return
        files = sorted(
            [p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"],
            key=lambda p: p.name,
        )
        encoded = quote(str(path))
        self._send_json(
            {
                "files": [
                    {
                        "name": p.name,
                        "size": p.stat().st_size,
                        "url": f"/api/download?folder={encoded}&name={quote(p.name)}",
                    }
                    for p in files
                ]
            }
        )

    def _download_file(self, folder: str, name: str) -> None:
        path = Path(folder) / Path(name).name
        if not is_allowed_dir(folder):
            self.send_error(403, "forbidden")
            return
        self._send_file(path, "video/mp4", as_attachment=Path(name).name)

    def _zip_output(self, folder: str) -> None:
        if not folder or not is_allowed_dir(folder):
            self._send_json({"ok": False, "error": "输出目录未登记或不存在"}, 403)
            return
        src = Path(folder)
        if not src.exists() or not src.is_dir():
            self._send_json({"ok": False, "error": "输出目录不存在"}, 404)
            return
        tmp = Path(tempfile_dir())
        tmp.mkdir(parents=True, exist_ok=True)
        zip_path = tmp / f"output_{time.strftime('%Y%m%d_%H%M%S')}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for p in sorted(src.rglob("*.mp4")):
                if p.is_file():
                    zf.write(p, p.relative_to(src).as_posix())
        self._send_file(zip_path, "application/zip", as_attachment=zip_path.name)

    def _serve_file(self, filename: str, mime: str = "text/html; charset=utf-8") -> None:
        path = WEB_DIR / filename
        if not path.exists():
            self.send_error(404, "not found")
            return
        self._send_file(path, mime)

    # ----------------------------------------------------------------
    # 任务控制
    # ----------------------------------------------------------------
    def _make_config(self, payload: dict) -> JobConfig | str:
        head_folder = str(payload.get("head_folder", "")).strip()
        tail_folder = str(payload.get("tail_folder", "")).strip()
        output_folder = str(payload.get("output_folder", "")).strip()
        if not head_folder or not tail_folder:
            return "请填写开头文件夹和结尾文件夹"
        if not os.path.isdir(head_folder) or not os.path.isdir(tail_folder):
            return "素材文件夹不存在，请检查路径"
        if not output_folder:
            return "请选择输出路径"
        count = _safe_int(payload.get("count", 10), 10, 1, 200)

        use_watermark = bool(payload.get("use_watermark", False))
        use_subtitle = bool(payload.get("use_subtitle", False))
        if use_subtitle and not subtitle_plugin.available():
            return "自动字幕需要 faster-whisper，请先运行：pip install faster-whisper"
        watermark_path = str(payload.get("watermark_path", "")).strip()
        if use_watermark and not watermark_path:
            return "已勾选水印，请填写水印图片路径"

        bgm_mode = str(payload.get("bgm_mode", "不使用"))
        bgm_path = str(payload.get("bgm_path", "")).strip()
        bgm_folder = str(payload.get("bgm_folder", "")).strip()
        fixed_bgm = str(payload.get("fixed_bgm") or "").strip() or None
        if bgm_mode == "本地导入" and not bgm_path:
            return "已选择本地导入 BGM，请填写音乐文件路径"
        if bgm_mode in {"音乐文件夹固定", "音乐文件夹随机"} and not bgm_folder:
            return "请选择音乐文件夹"
        if bgm_mode == "音乐文件夹固定" and not fixed_bgm:
            return "请选择固定 BGM"

        middle_folder = str(payload.get("middle_folder", "")).strip()
        fixed_middle = str(payload.get("fixed_middle") or "").strip() or None
        middle_items = [str(x).strip() for x in payload.get("middle_items", []) if str(x).strip()]
        middle_items = middle_items[:10]
        middle_count_raw = payload.get("middle_count")
        middle_count = None if middle_count_raw is None else _safe_int(middle_count_raw, 1, 0, 10)

        # 多中间素材池（用户可添加多个池，按池顺序插入头尾之间）
        middle_pools_raw = payload.get("middle_pools")
        middle_pools: list[dict] = []
        if isinstance(middle_pools_raw, list):
            for pool in middle_pools_raw[:5]:
                pool_folder = str(pool.get("folder", "")).strip()
                if not pool_folder:
                    continue
                if not os.path.isdir(pool_folder):
                    return f"中间素材池文件夹不存在：{pool_folder}"
                pool_items = [str(x).strip() for x in pool.get("items", []) if str(x).strip()][:10]
                pool_count_raw = pool.get("count")
                pool_count = None if pool_count_raw is None else _safe_int(pool_count_raw, 1, 0, 10)
                middle_pools.append({"folder": pool_folder, "items": pool_items, "count": pool_count})
        if middle_pools:
            # 多池模式下，旧字段取第一个池的值（保持兼容），引擎优先使用 middle_pools
            middle_folder = middle_pools[0]["folder"]
            middle_items = middle_pools[0]["items"]
            middle_count = middle_pools[0]["count"]
            fixed_middle = None
        else:
            if fixed_middle and not middle_items:
                middle_items = [fixed_middle]  # 兼容旧配置
            if middle_items and not middle_folder:
                return "已勾选固定中间素材，请先填写中间素材文件夹"
            if middle_count and not middle_folder:
                return "已设置随机中间片段，请先填写中间素材文件夹"

        return JobConfig(
            head_folder=head_folder,
            tail_folder=tail_folder,
            fixed_head=str(payload.get("fixed_head") or "").strip() or None,
            fixed_tail=str(payload.get("fixed_tail") or "").strip() or None,
            output_folder=output_folder,
            count=count,
            resolution=str(payload.get("resolution", "1080x1920")),
            duration_mode=str(payload.get("duration_mode", "不限制")),
            use_watermark=use_watermark,
            watermark_path=watermark_path,
            use_transition=bool(payload.get("use_transition", False)),
            transition_mode=str(payload.get("transition_mode", "不使用")),
            transition_type=str(payload.get("transition_type", "fade")),
            transition_duration=_safe_float(payload.get("transition_duration", 0.5), 0.5, 0.1, 2.0),
            transition_types=[str(x) for x in payload.get("transition_types", [])],
            bgm_mode=bgm_mode,
            bgm_path=bgm_path,
            bgm_volume=_safe_float(payload.get("bgm_volume", 0.2), 0.2, 0.0, 1.0),
            bgm_folder=bgm_folder,
            fixed_bgm=fixed_bgm,
            normalize_audio=bool(payload.get("normalize_audio", False)),
            bgm_fade=bool(payload.get("bgm_fade", False)),
            bgm_ducking=bool(payload.get("bgm_ducking", False)),
            fit_mode=str(payload.get("fit_mode", "fit")),
            output_name_template=str(payload.get("output_name_template", "output_{序号}_{开头}_{结尾}")),
            random_seed=_safe_int(payload.get("random_seed", 20260905), 20260905),
            dedupe_enabled=bool(payload.get("dedupe_enabled", True)),
            middle_folder=middle_folder,
            fixed_middle=fixed_middle,
            middle_items=middle_items,
            middle_count=middle_count,
            middle_pools=middle_pools,
            use_subtitle=use_subtitle,
            watermark_mode=str(payload.get("watermark_mode", "铺满全屏")),
            watermark_position=str(payload.get("watermark_position", "右下角")),
            watermark_scale=_safe_float(payload.get("watermark_scale", 0.15), 0.15, 0.05, 0.6),
            watermark_opacity=_safe_float(payload.get("watermark_opacity", 0.6), 0.6, 0.05, 1.0),
            workers=_safe_int(payload.get("workers", 2), 2, 1, 8),
        )

    def _run_task(self, config: JobConfig, label: str, mode: str, failed_items: list[dict] | None = None) -> None:
        STATE.cancel_event.clear()
        STATE.pause_event.clear()
        STATE.last_config = config
        STATE.last_failed_items = []
        STATE.result = None
        STATE.error = None
        STATE.logs = []
        STATE.paused = False
        STATE.running = True

        total = len(failed_items) if mode == "retry" else config.count
        STATE.begin(total)
        STATE.add_log(f"{label}开始，共 {total} 条")

        def log(message: str) -> None:
            STATE.add_log(message)

        def progress(current: int, total_: int) -> None:
            STATE.set_progress(current, total_)

        def worker() -> None:
            try:
                if mode == "retry":
                    result = process_failed_items(
                        config, failed_items or [], STATE.cancel_event, STATE.pause_event,
                        log=log, progress=progress,
                    )
                else:
                    result = process_batch(
                        config, STATE.cancel_event, STATE.pause_event, log=log, progress=progress,
                    )
                STATE.result = result
                STATE.last_failed_items = list(result.failed_items)
                record = {
                    "type": mode,
                    "config": asdict(config),
                    "success": result.success,
                    "skipped": result.skipped,
                    "failed": result.failed,
                    "cancelled": result.cancelled,
                }
                save_history(record)
            except MediaError as exc:
                STATE.error = str(exc)
                STATE.add_log(str(exc))
            except Exception as exc:
                STATE.error = f"程序异常：{exc}"
                STATE.add_log(STATE.error)
            finally:
                STATE.running = False
                STATE.end()

        STATE.worker = threading.Thread(target=worker, daemon=True)
        STATE.worker.start()

    def _start(self) -> None:
        if STATE.running:
            self._send_json({"ok": False, "error": "任务正在运行"})
            return
        payload = self._read_json()
        config = self._make_config(payload)
        if isinstance(config, str):
            self._send_json({"ok": False, "error": config})
            return
        register_allowed_dir(config.output_folder)
        self._run_task(config, "任务", "batch")
        self._send_json({"ok": True})

    def _preview(self) -> None:
        if STATE.running:
            self._send_json({"ok": False, "error": "任务正在运行"})
            return
        payload = self._read_json()
        config = self._make_config(payload)
        if isinstance(config, str):
            self._send_json({"ok": False, "error": config})
            return
        config.count = 1
        config.output_folder = str(PREVIEW_DIR)
        config.output_name_template = "preview_{序号}_{开头}_{结尾}"
        register_allowed_dir(str(PREVIEW_DIR))
        self._run_task(config, "预览", "preview")
        self._send_json({"ok": True})

    def _retry_failed(self) -> None:
        if STATE.running:
            self._send_json({"ok": False, "error": "任务正在运行"})
            return
        config = STATE.last_config
        failed_items = STATE.last_failed_items
        if not config or not failed_items:
            self._send_json({"ok": False, "error": "没有可重试的失败项"})
            return
        register_allowed_dir(config.output_folder)
        self._run_task(config, f"重试 {len(failed_items)} 个失败项", "retry", failed_items)
        self._send_json({"ok": True})

    def _toggle_pause(self) -> None:
        if not STATE.running:
            STATE.paused = False
            return
        if STATE.paused:
            STATE.pause_event.clear()
            STATE.paused = False
            STATE.add_log("已继续")
        else:
            STATE.pause_event.set()
            STATE.paused = True
            STATE.add_log("已暂停，当前素材处理完成后暂停")


def tempfile_dir() -> str:
    import tempfile as _tf
    return _tf.gettempdir()


def main() -> None:
    register_standard_dirs()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"网页版已启动：{url}")
    if getattr(sys, "frozen", False):
        # 便携版：启动后自动打开默认浏览器
        threading.Timer(1.0, lambda: __import__("webbrowser").open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
