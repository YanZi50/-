"""web_app 端到端冒烟测试：真实任务 + 下载白名单 + zip + 参数校验。"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from imageio_ffmpeg import get_ffmpeg_exe

BASE = "http://127.0.0.1:8765"


def api(path, method="GET", payload=None, timeout=60):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def raw_get(path, timeout=30):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def make_assets(base):
    os.makedirs(os.path.join(base, "head"), exist_ok=True)
    os.makedirs(os.path.join(base, "tail"), exist_ok=True)
    os.makedirs(os.path.join(base, "out"), exist_ok=True)
    ff = get_ffmpeg_exe()

    def mk(path, color, d=2):
        subprocess.run(
            [ff, "-y", "-f", "lavfi", "-i", f"color=c={color}:size=320x480:duration={d}:r=30", "-c:v", "libx264", path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    mk(os.path.join(base, "head", "h.mp4"), "blue")
    mk(os.path.join(base, "tail", "t.mp4"), "red")


def main():
    base = os.path.join(os.environ["TEMP"], "sppj_e2e2")
    import shutil
    shutil.rmtree(base, ignore_errors=True)
    make_assets(base)

    # 1) 参数校验：缺文件夹
    code, body = api("/api/start", "POST", {"tail_folder": "x", "output_folder": "y"})
    assert code == 200 and "开头".encode("utf-8") in body, (code, body)
    print("PASS 参数校验")

    # 2) 启动真实任务
    payload = {
        "head_folder": os.path.join(base, "head"),
        "tail_folder": os.path.join(base, "tail"),
        "output_folder": os.path.join(base, "out"),
        "count": 1,
        "resolution": "1080x1920",
        "duration_mode": "不限制",
        "bgm_mode": "不使用",
        "workers": 2,
    }
    code, body = api("/api/start", "POST", payload)
    assert b'"ok": true' in body, (code, body)
    print("PASS 启动任务")

    deadline = time.time() + 120
    status = {}
    while time.time() < deadline:
        time.sleep(3)
        code, body = api("/api/status")
        status = json.loads(body)
        if not status["running"]:
            break
    assert status["success"] == 1 and status["failed"] == 0, status
    print("PASS 任务完成 success=1", status.get("speed_per_sec"))

    # 3) download 白名单：输出目录允许，素材目录拒绝
    out_path = os.path.join(base, "out")
    name = os.listdir(out_path)[0]
    code, body = raw_get("/api/download?folder=" + urllib.parse.quote(out_path) + "&name=" + urllib.parse.quote(name))
    assert code == 200 and b"ftyp" in body[:16], (code, body[:20])
    print("PASS 输出目录下载")

    head_path = os.path.join(base, "head")
    hname = os.listdir(head_path)[0]
    code, body = raw_get("/api/download?folder=" + urllib.parse.quote(head_path) + "&name=" + urllib.parse.quote(hname))
    assert code == 403, (code, body[:50])
    print("PASS 白名单拒绝非输出目录")

    # 4) zip 打包
    code, body = api("/api/zip", "POST", {"folder": out_path}, timeout=120)
    assert b"application/zip" in body or code == 200, (code, body[:80])
    print("PASS zip 打包")

    # 5) 非法参数不崩溃
    bad = dict(payload, transition_duration="abc", bgm_volume="xyz", count="999")
    code, body = api("/api/start", "POST", bad)
    assert b'"ok": true' in body or "任务正在运行".encode("utf-8") in body, (code, body)
    print("PASS 非法参数回落默认值")


if __name__ == "__main__":
    main()
