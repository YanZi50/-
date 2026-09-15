# -*- coding: utf-8 -*-
"""前端 E2E 回归冒烟测试（Playwright + 复用系统 Chrome）。

覆盖高风险回归点：
  1. 页面加载无 JS 错误（专治 Vue 模板语法错误导致的"无声白屏"）
  2. 素材扫描后素材卡/音量滑块正常渲染
  3. 音量滑块实时显示 + 双击恢复默认
  4. 预览两次均重新生成（不"跳过"）

运行：python tests/e2e_ui.py   （约 30-60s）
依赖：pip install playwright（复用系统 Chrome，无需下载浏览器）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # 未安装时给出可执行提示，不影响其它测试收集
    sync_playwright = None

from imageio_ffmpeg import get_ffmpeg_exe  # noqa: E402


class _Service:
    """独立启动一个 web_app 实例（自动端口），提供 base_url；退出时关闭。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.base_url = ""

    def start(self) -> None:
        env = dict(os.environ)
        proc = subprocess.Popen(
            [sys.executable, "web_app.py"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        self.proc = proc
        # 服务端 stdout 同时落盘，便于失败时定位服务端异常
        self._log_path = Path(tempfile.gettempdir()) / "e2e_server_out.txt"
        try:
            self._log_fh = open(self._log_path, "w", encoding="utf-8")
        except Exception:
            self._log_fh = None
        # 轮询 stdout 直到出现 "网页版已启动：http://127.0.0.1:PORT"
        deadline = time.time() + 30
        buf = ""
        while time.time() < deadline:
            line = proc.stdout.readline()  # 行缓冲
            if not line:
                if proc.poll() is not None:
                    raise RuntimeError("服务启动失败，进程已退出")
                time.sleep(0.2)
                continue
            buf += line
            if self._log_fh:
                self._log_fh.write(line)
                self._log_fh.flush()
            m = re.search(r"网页版已启动：(\S+)", line)
            if m:
                self.base_url = m.group(1).rstrip()
                # 自检：确认自己的实例真的可服务（端口可能被残留进程抢先占用）
                self._wait_ping()
                return
        raise RuntimeError(f"30s 内未等到服务就绪，输出：{buf[-500:]}")

    def _wait_ping(self, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self.base_url + "/api/ping", timeout=1):
                    return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError(f"服务实例 {self.base_url} 自检失败（ping 不通）")

    def alive(self) -> bool:
        if not self.base_url:
            return False
        try:
            with urllib.request.urlopen(self.base_url + "/api/ping", timeout=1):
                return True
        except Exception:
            return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None


def _make_materials(base: Path) -> None:
    """生成 1 头 + 1 尾测试素材（2s，带正弦音频）。"""
    ff = get_ffmpeg_exe()
    head, tail = base / "head", base / "tail"
    head.mkdir(parents=True), tail.mkdir(parents=True)
    for folder, pattern, freq in ((head, "testsrc", "440"), (tail, "smptebars", "660")):
        subprocess.run(
            [ff, "-y", "-f", "lavfi", "-i", f"{pattern}=size=320x568:rate=25:duration=2",
             "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
             str(folder / "m1.mp4")],
            capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


class E2EUITests(unittest.TestCase):
    """E2E 用例。全部依赖独立服务实例与临时素材。"""

    @classmethod
    def setUpClass(cls) -> None:
        if sync_playwright is None:
            raise unittest.SkipTest("未安装 playwright：pip install playwright")
        cls._tmp = Path(tempfile.mkdtemp(prefix="sppj_e2e_"))
        _make_materials(cls._tmp)
        cls.svc = _Service()
        cls.svc.start()
        cls.url = cls.svc.base_url

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "svc", None):
            cls.svc.stop()
        if getattr(cls, "_tmp", None):
            shutil.rmtree(cls._tmp, ignore_errors=True)

    # ---- 工具 ----
    def _page(self, p):
        # 复用系统 Edge（chromium 内核），无需下载浏览器；无 Edge 时回退 Playwright 自带 chromium
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors: list[str] = []
        # 忽略 favicon/静态资源 404 误报，只收 JS 运行期错误与页面崩溃
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" and "404 (Not Found)" not in m.text else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        return browser, page, errors

    def _fill_path(self, page, sel: str, path: str) -> None:
        page.fill(sel, str(path).replace("\\", "\\\\"))
        page.eval_on_selector(sel, "el => el.dispatchEvent(new Event('change', {bubbles:true}))")

    def _scan_materials(self, page) -> None:
        self._fill_path(page, "#input_head", str(self._tmp / "head"))
        self._fill_path(page, "#input_tail", str(self._tmp / "tail"))
        page.wait_for_timeout(3000)  # 扫描 + 并发探测（含缩略图）
        # 素材卡在"展开"后才渲染：点击开头素材库的展开按钮
        try:
            card = page.locator(".card", has_text="开头素材库")
            card.get_by_role("button", name="展开").first.click(timeout=5000)
            page.wait_for_timeout(1500)
        except Exception:
            pass  # 已展开/布局变化时忽略

    def _api(self, route: str, payload: dict | None = None) -> dict:
        if payload is None:
            req = urllib.request.Request(self.url + route)  # GET-only 路由（/api/status 等）
        else:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(self.url + route, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise AssertionError(f"API {route} 返回 {e.code}：{body}（url={self.url}）") from e

    def _wait_job_idle(self) -> dict:
        for _ in range(60):
            st = self._api("/api/status")
            if not st.get("running") and not st.get("deduping"):
                return st
            time.sleep(1)
        raise TimeoutError("任务 60s 未结束")

    def _api_debug(self) -> None:
        """失败时打印服务健康信息，便于定位端口/实例问题。"""
        try:
            with urllib.request.urlopen(self.url + "/api/ping", timeout=2) as r:
                print(f"[diag] ping {r.status}")
            with urllib.request.urlopen(self.url + "/api/update", timeout=2) as r:
                print(f"[diag] update {r.read().decode()[:120]}")
        except Exception as e:
            print(f"[diag] 服务不可达: {e}")

    # ---- 用例 ----
    def test_01_page_loads_no_js_error(self) -> None:
        """页面加载：无 JS 报错（白屏检测）+ 关键 UI 渲染。"""
        with sync_playwright() as p:
            browser, page, errors = self._page(p)
            page.goto(self.url, timeout=20000)
            page.wait_for_selector("text=信息流素材拼接", timeout=15000)
            page.wait_for_timeout(500)
            self.assertEqual([], errors, f"页面存在 JS 错误：{errors}")
            self.assertIn("素材库", page.inner_text("body"))
            browser.close()

    def test_02_material_scan_and_volume_slider(self) -> None:
        """素材扫描：素材卡渲染 + 音量滑块存在。"""
        with sync_playwright() as p:
            browser, page, errors = self._page(p)
            page.goto(self.url, timeout=20000)
            page.wait_for_selector("#input_head", timeout=15000)
            self._scan_materials(page)
            page.wait_for_selector(".material-card", timeout=15000)
            cards = page.query_selector_all(".material-card")
            self.assertGreaterEqual(len(cards), 1, "素材卡未渲染")
            sliders = page.query_selector_all(".vol-slider")
            self.assertGreaterEqual(len(sliders), 1, "音量滑块未渲染")
            self.assertEqual([], errors, f"页面存在 JS 错误：{errors}")
            browser.close()

    def test_03_volume_interactive(self) -> None:
        """音量滑块：拖动实时显示 + 双击恢复默认 1.00。"""
        with sync_playwright() as p:
            browser, page, _ = self._page(p)
            page.goto(self.url, timeout=20000)
            page.wait_for_selector("#input_head", timeout=15000)
            self._scan_materials(page)
            page.wait_for_selector(".vol-slider", timeout=15000)
            page.eval_on_selector(".vol-slider", "el => { el.value='0.5'; el.dispatchEvent(new Event('input', {bubbles:true})); }")
            page.wait_for_timeout(400)  # Vue nextTick flush
            self.assertEqual("0.50", page.inner_text(".vol-val").strip(), "拖动后未实时显示 0.50")
            page.eval_on_selector(".vol-slider", "el => el.dispatchEvent(new MouseEvent('dblclick', {bubbles:true}))")
            page.wait_for_timeout(400)
            self.assertEqual("1.00", page.inner_text(".vol-val").strip(), "双击后未恢复 1.00")
            browser.close()

    def test_04_preview_always_regenerates(self) -> None:
        """预览两次：均重新生成（success=1 且 skipped=0），不"跳过"。"""
        self.assertTrue(self.svc.alive(), f"服务实例已不可达：{self.svc.base_url}")
        for i in (1, 2):
            self._api("/api/preview", {
                "head_folder": str(self._tmp / "head"),
                "tail_folder": str(self._tmp / "tail"),
                "output_folder": str(self._tmp / "out"),
                "count": 1, "workers": 1, "resolution": "1080x1920",
                "duration_mode": "不限制", "transition_mode": "不使用",
                "dedupe_enabled": False, "dedupe_level": "off",
            })
            st = self._wait_job_idle()
            self.assertEqual(1, st.get("success"), f"第{i}次预览未成功：{st}")
            self.assertEqual(0, st.get("skipped"), f"第{i}次预览被跳过（应强制重新生成）")

if __name__ == "__main__":
    unittest.main(verbosity=2)
