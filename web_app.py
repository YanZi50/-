import json
import os
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from config_store import load_last_config, save_last_config
from history_store import list_history, save_history
from template_store import delete_template, list_templates, load_template, save_template

from video_engine import BatchResult, JobConfig, MediaError, process_batch, process_failed_items, scan_videos


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

    def add_log(self, message: str) -> None:
        with self.lock:
            self.logs.append(message)
            self.logs = self.logs[-1000:]

    def set_progress(self, current: int, total: int) -> None:
        with self.lock:
            self.current = current
            self.total = total

    def status_dict(self) -> dict:
        with self.lock:
            return {
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


STATE = AppState()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: ANN001
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_file("index.html")
            return
        if parsed.path == "/api/templates":
            self._send_json({"templates": list_templates()})
            return
        if parsed.path == "/api/history":
            self._send_json({"history": list_history()})
            return
        if parsed.path == "/api/config/load":
            self._send_json(load_last_config())
            return
        if parsed.path == "/api/status":
            self._send_json(STATE.status_dict())
            return
        if parsed.path == "/api/scan":
            query = parse_qs(parsed.query)
            head = (query.get("head") or [""])[0]
            tail = (query.get("tail") or [""])[0]
            middle = (query.get("middle") or [""])[0]
            head_files = scan_videos(head)
            tail_files = scan_videos(tail)
            middle_files = scan_videos(middle)
            self._send_json(
                {
                    "head": [{"name": Path(p).name, "path": p} for p in head_files],
                    "tail": [{"name": Path(p).name, "path": p} for p in tail_files],
                    "middle": [{"name": Path(p).name, "path": p} for p in middle_files],
                }
            )
            return
        if parsed.path == "/api/upload_path":
            query = parse_qs(parsed.query)
            kind = (query.get("kind") or ["head"])[0]
            path = self._upload_folder_for_kind(kind)
            self._send_json({"path": str(path)})
            return
        if parsed.path == "/api/list_output":
            query = parse_qs(parsed.query)
            folder = (query.get("folder") or [""])[0]
            self._list_output(folder)
            return
        if parsed.path == "/api/download":
            query = parse_qs(parsed.query)
            folder = (query.get("folder") or [""])[0]
            name = (query.get("name") or [""])[0]
            self._download_file(folder, name)
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/templates/save":
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
        if parsed.path == "/api/templates/load":
            payload = self._read_json()
            name = str(payload.get("name", "")).strip()
            self._send_json(load_template(name))
            return
        if parsed.path == "/api/templates/delete":
            payload = self._read_json()
            name = str(payload.get("name", "")).strip()
            self._send_json({"ok": delete_template(name)})
            return
        if parsed.path == "/api/config/save":
            payload = self._read_json()
            path = save_last_config(payload)
            self._send_json({"ok": True, "path": str(path)})
            return
        if parsed.path == "/api/preview":
            self._preview()
            return
        if parsed.path == "/api/start":
            self._start()
            return
        if parsed.path == "/api/retry_failed":
            self._retry_failed()
            return
        if parsed.path == "/api/cancel":
            STATE.cancel_event.set()
            STATE.add_log("已请求取消")
            self._send_json({"ok": True})
            return
        if parsed.path == "/api/pause":
            self._toggle_pause()
            self._send_json({"paused": STATE.paused})
            return
        if parsed.path == "/api/upload":
            self._upload_file(parsed)
            return
        self._send_json({"error": "not found"}, 404)

    def _upload_folder_for_kind(self, kind: str) -> Path:
        if kind == "head":
            folder = UPLOAD_ROOT / "head"
        elif kind == "tail":
            folder = UPLOAD_ROOT / "tail"
        elif kind == "middle":
            folder = UPLOAD_ROOT / "middle"
        elif kind in {"watermark", "bgm"}:
            folder = UPLOAD_ROOT / "files"
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
                        "url": f"/api/download?folder={encoded}&name={quote(p.name)}",
                    }
                    for p in files
                ]
            }
        )

    def _download_file(self, folder: str, name: str) -> None:
        path = Path(folder) / Path(name).name
        if not path.exists() or not path.is_file():
            self.send_error(404, "not found")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{Path(name).name}"',
        )
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, filename: str) -> None:
        path = WEB_DIR / filename
        if not path.exists():
            self.send_error(404, "not found")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _start(self) -> None:
        if STATE.running:
            self._send_json({"ok": False, "error": "任务正在运行"})
            return
        payload = self._read_json()
        config = self._make_config(payload)
        if isinstance(config, str):
            self._send_json({"ok": False, "error": config})
            return

        STATE.cancel_event.clear()
        STATE.pause_event.clear()
        STATE.last_config = config
        STATE.last_failed_items = []
        STATE.result = None
        STATE.error = None
        STATE.current = 0
        STATE.total = 0
        STATE.logs = []
        STATE.paused = False
        STATE.running = True
        STATE.add_log("任务开始")

        def log(message: str) -> None:
            STATE.add_log(message)

        def progress(current: int, total: int) -> None:
            STATE.set_progress(current, total)

        def worker() -> None:
            try:
                result = process_batch(
                    config,
                    STATE.cancel_event,
                    STATE.pause_event,
                    log=log,
                    progress=progress,
                )
                STATE.result = result
                STATE.last_failed_items = list(result.failed_items)
                save_history(
                    {
                        "type": "retry_failed",
                        "config": asdict(config),
                        "success": result.success,
                        "skipped": result.skipped,
                        "failed": result.failed,
                        "cancelled": result.cancelled,
                    }
                )
                save_history(
                    {
                        "type": "batch",
                        "config": asdict(config),
                        "success": result.success,
                        "skipped": result.skipped,
                        "failed": result.failed,
                        "cancelled": result.cancelled,
                    }
                )
            except MediaError as exc:
                STATE.error = str(exc)
                STATE.add_log(str(exc))
            except Exception as exc:
                STATE.error = f"程序异常：{exc}"
                STATE.add_log(STATE.error)
            finally:
                STATE.running = False

        STATE.worker = threading.Thread(target=worker, daemon=True)
        STATE.worker.start()
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
        config.output_name_template = "preview"
        STATE.cancel_event.clear()
        STATE.pause_event.clear()
        STATE.result = None
        STATE.error = None
        STATE.current = 0
        STATE.total = 1
        STATE.logs = []
        STATE.paused = False
        STATE.running = True
        STATE.last_config = config
        STATE.last_failed_items = []
        STATE.add_log("开始生成预览")

        def log(message: str) -> None:
            STATE.add_log(message)

        def progress(current: int, total: int) -> None:
            STATE.set_progress(current, total)

        def worker() -> None:
            try:
                result = process_batch(config, STATE.cancel_event, STATE.pause_event, log=log, progress=progress)
                STATE.result = result
                STATE.last_failed_items = list(result.failed_items)
            except MediaError as exc:
                STATE.error = str(exc)
                STATE.add_log(str(exc))
            except Exception as exc:
                STATE.error = f"程序异常：{exc}"
                STATE.add_log(STATE.error)
            finally:
                STATE.running = False

        STATE.worker = threading.Thread(target=worker, daemon=True)
        STATE.worker.start()
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
        STATE.cancel_event.clear()
        STATE.pause_event.clear()
        STATE.result = None
        STATE.error = None
        STATE.current = 0
        STATE.total = len(failed_items)
        STATE.logs = []
        STATE.paused = False
        STATE.running = True
        STATE.add_log(f"开始重试 {len(failed_items)} 个失败项")

        def log(message: str) -> None:
            STATE.add_log(message)

        def progress(current: int, total: int) -> None:
            STATE.set_progress(current, total)

        def worker() -> None:
            try:
                result = process_failed_items(
                    config,
                    failed_items,
                    STATE.cancel_event,
                    STATE.pause_event,
                    log=log,
                    progress=progress,
                )
                STATE.result = result
                STATE.last_failed_items = list(result.failed_items)
                save_history(
                    {
                        "type": "retry_failed",
                        "config": asdict(config),
                        "success": result.success,
                        "skipped": result.skipped,
                        "failed": result.failed,
                        "cancelled": result.cancelled,
                    }
                )
                save_history(
                    {
                        "type": "batch",
                        "config": asdict(config),
                        "success": result.success,
                        "skipped": result.skipped,
                        "failed": result.failed,
                        "cancelled": result.cancelled,
                    }
                )
            except MediaError as exc:
                STATE.error = str(exc)
                STATE.add_log(str(exc))
            except Exception as exc:
                STATE.error = f"程序异常：{exc}"
                STATE.add_log(STATE.error)
            finally:
                STATE.running = False

        STATE.worker = threading.Thread(target=worker, daemon=True)
        STATE.worker.start()
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

    def _make_config(self, payload: dict) -> JobConfig | str:
        head_folder = str(payload.get("head_folder", "")).strip()
        tail_folder = str(payload.get("tail_folder", "")).strip()
        output_folder = str(payload.get("output_folder", "")).strip()
        if not head_folder or not tail_folder:
            return "请填写开头文件夹和结尾文件夹"
        if not os.path.isdir(head_folder) or not os.path.isdir(tail_folder):
            return "素材文件夹不存在，请检查路径"
        if not output_folder:
            output_folder = str(Path(head_folder).parent / "output")
        try:
            count = max(1, min(200, int(payload.get("count", 10))))
        except Exception:
            return "出片数量必须是 1–200 之间的整数"

        use_watermark = bool(payload.get("use_watermark", False))
        watermark_path = str(payload.get("watermark_path", "")).strip()
        if use_watermark and not watermark_path:
            return "已勾选水印，请填写水印图片路径"

        bgm_mode = str(payload.get("bgm_mode", "不使用"))
        bgm_path = str(payload.get("bgm_path", "")).strip()
        if bgm_mode == "本地导入" and not bgm_path:
            return "已选择本地导入 BGM，请填写音乐文件路径"

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
            bgm_mode=bgm_mode,
            bgm_path=bgm_path,
            bgm_volume=float(payload.get("bgm_volume", 0.2)),
            normalize_audio=bool(payload.get("normalize_audio", False)),
            bgm_fade=bool(payload.get("bgm_fade", False)),
            bgm_ducking=bool(payload.get("bgm_ducking", False)),
            output_name_template=str(payload.get("output_name_template", "output_{序号}_{开头}_{结尾}")),
            random_seed=int(payload.get("random_seed", 20260905)),
            dedupe_enabled=bool(payload.get("dedupe_enabled", True)),
            middle_folder=str(payload.get("middle_folder", "")).strip(),
            fixed_middle=str(payload.get("fixed_middle") or "").strip() or None,
        )


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"网页版已启动：http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
