# -*- coding: utf-8 -*-
"""video_core：video_engine 的纯决策逻辑层（无 ffmpeg/IO 副作用）。

包含：素材扫描、内容指纹去重、组合生成、中间序列、转场/差异化决策、
BGM 选择、输出命名、参数解析。这些函数自包含、可独立测试，
被 video_engine（ffmpeg 执行层）单向引用，不反向依赖。
"""
import hashlib
import os
import random
import time
from pathlib import Path
from typing import Optional

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".ts", ".m4v", ".webm", ".flv", ".wmv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def scan_videos(folder: str) -> list[str]:
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        return []
    items = sorted(
        [str(p) for p in path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS],
        key=lambda x: Path(x).name.lower(),
    )
    return items


def scan_audio(folder: str) -> list[str]:
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        return []
    items = sorted(
        [str(p) for p in path.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS],
        key=lambda x: Path(x).name.lower(),
    )
    return items


def media_fingerprint(path: str) -> str:
    """轻量内容指纹：文件大小 + 头尾各 1MB 的 SHA256。
    同内容素材（不同文件名/不同路径）会得到相同指纹，用于去重识别。"""
    try:
        p = Path(path)
        size = p.stat().st_size
        hasher = hashlib.sha256()
        hasher.update(str(size).encode())
        chunk = 1024 * 1024
        with p.open("rb") as f:
            head = f.read(chunk)
            hasher.update(head)
            if size > chunk:
                f.seek(max(0, size - chunk))
                tail = f.read(chunk)
                hasher.update(tail)
        return hasher.hexdigest()
    except Exception:
        # 读取失败（如占用/权限）时退化为按路径指纹，不影响功能
        return "path:" + path.lower().replace("\\", "/")


def dedupe_by_fp(files: list[str]) -> list[str]:
    """按内容指纹去重，保留每个指纹的第一个文件。"""
    seen: set[str] = set()
    result: list[str] = []
    for f in files:
        fp = media_fingerprint(f)
        if fp in seen:
            continue
        seen.add(fp)
        result.append(f)
    return result


def dedupe_delta(level: str, options: dict, seed: int) -> dict:
    """按强度/选项/种子确定性生成一条成片的差异化参数。
    level=off 或空参数时返回空 dict（不启用差异化）。"""
    if level not in {"light", "deep"}:
        return {}
    opts = options or {}
    rng = random.Random(seed)
    d: dict = {}
    strength = str(opts.get("deep_strength") or "medium")
    s_amp = {"low": 0.010, "medium": 0.020, "high": 0.030}.get(strength, 0.020)
    n_range = {"low": (3, 6), "medium": (6, 10), "high": (10, 16)}.get(strength, (6, 10))
    if opts.get("speed"):
        sign = 1.0 if rng.random() < 0.5 else -1.0
        d["speed"] = round(1.0 + sign * rng.uniform(0.5 * s_amp, s_amp), 4)
    if opts.get("mirror"):
        d["mirror"] = True
    if opts.get("noise"):
        d["noise"] = rng.randint(n_range[0], n_range[1])
    if opts.get("pitch"):
        sign = 1.0 if rng.random() < 0.5 else -1.0
        d["pitch"] = round(1.0 + sign * rng.uniform(0.5 * s_amp, s_amp), 4)
    if opts.get("visual"):
        amp = 0.05 if level == "light" else 0.10
        d["visual"] = True
        d["brightness"] = round(rng.uniform(-amp, amp), 4)
        d["contrast"] = round(rng.uniform(1 - amp, 1 + amp), 4)
        d["saturation"] = round(rng.uniform(1 - amp, 1 + amp), 4)
        zoom = round(rng.uniform(1.0, 1.03 if level == "light" else 1.08), 4)
        d["zoom"] = zoom
        if zoom > 1.0:
            # 保守偏移范围（≤ 帧宽的 (zoom-1) 倍，任何分辨率都不会超出）
            d["crop_x"] = rng.randint(0, max(1, int(1000 * (zoom - 1))))
            d["crop_y"] = rng.randint(0, max(1, int(1000 * (zoom - 1))))
    if opts.get("segment"):
        d["offset"] = round(rng.uniform(0.05, 0.25 if level == "light" else 0.45), 3)
        d["random_transition"] = level == "deep"
    if opts.get("audio"):
        d["bgm_shift_seed"] = seed + 999
    if d.get("random_transition"):
        # 深度差异化：确定性生成该组合的随机转场（类型+时长）
        types = ["fade", "dissolve", "slideleft", "slideright", "wipeleft", "wiperight",
                 "circleopen", "circleclose", "smoothleft", "smoothright", "zoomin", "fadeblack"]
        trng = random.Random(seed + 31)
        d["transition"] = trng.choice(types)
        d["transition_duration"] = round(trng.uniform(0.3, 0.8), 3)
    return d


def build_combinations(
    head_files: list[str],
    tail_files: list[str],
    fixed_head: Optional[str],
    fixed_tail: Optional[str],
    count: int,
    seed: int = 20260905,
    dedupe_enabled: bool = True,
) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    if fixed_head and fixed_tail:
        return [(fixed_head, fixed_tail)]
    if fixed_head:
        unique = list(dict.fromkeys(tail_files))
        rng.shuffle(unique)
        # 组合数不足时不重复循环，用多少给多少（防止产出重复成片）
        return [(fixed_head, tail) for tail in unique[:count]]
    if fixed_tail:
        unique = list(dict.fromkeys(head_files))
        rng.shuffle(unique)
        return [(head, fixed_tail) for head in unique[:count]]

    pairs = [(h, t) for h in head_files for t in tail_files]
    if not pairs:
        return []
    if not dedupe_enabled:
        return [(rng.choice(head_files), rng.choice(tail_files)) for _ in range(count)]

    batch = list(pairs)
    rng.shuffle(batch)
    # 组合数不足时返回全部可用组合，不循环复用（防止同一组合重复出片）
    return batch[:count]


# ============================================================
# 插件注册表（唯一权威来源）
# 新增转场：TRANSITION_REGISTRY 加一行（value 必须是 ffmpeg xfade 支持的 transition 名）
# 新增去重维度：DEDUPE_REGISTRY 加一行，并在 dedupe_delta 中实现对应分支
# 前端通过 /api/options 动态获取本表，刷新即生效（无需改前端）
# ============================================================
TRANSITION_REGISTRY: dict[str, str] = {
    "fade": "淡入淡出", "dissolve": "溶解",
    "slideleft": "左滑", "slideright": "右滑", "slideup": "上滑", "slidedown": "下滑",
    "wipeleft": "左擦除", "wiperight": "右擦除", "wipeup": "上擦除", "wipedown": "下擦除",
    "circleopen": "圆形打开", "circleclose": "圆形关闭", "circlecrop": "圆形裁剪",
    "smoothleft": "平滑左移", "smoothright": "平滑右移", "smoothup": "平滑上移", "smoothdown": "平滑下移",
    "diagtl": "对角(左上)", "diagtr": "对角(右上)", "diagbl": "对角(左下)", "diagbr": "对角(右下)",
    "zoomin": "放大进入", "pixelize": "像素化",
    "fadeblack": "黑场淡变", "fadewhite": "白场淡变",
    "coverleft": "左覆盖", "coverright": "右覆盖", "coverup": "上覆盖", "coverdown": "下覆盖",
    "revealleft": "左揭示", "revealright": "右揭示", "revealup": "上揭示", "revealdown": "下揭示",
    "vertopen": "垂直打开", "vertclose": "垂直关闭",
    "horzopen": "水平打开", "horzclose": "水平关闭",
    "squeezeh": "水平挤压", "squeezev": "垂直挤压",
    "radial": "放射状",
    "hlslice": "水平切片", "hrslice": "垂直切片",
    "vuslice": "垂直切片(上)", "vdslice": "垂直切片(下)",
    "hblur": "水平模糊",
    "wipetl": "对角擦除(左上)", "wipetr": "对角擦除(右上)",
    "fadegrays": "灰度淡变",
}

DEDUPE_REGISTRY: dict[str, dict] = {
    "visual": {"name": "画面微调", "desc": "亮度/对比度/饱和度 ±5-10%，随机裁切缩放", "deep": False},
    "segment": {"name": "片段差异化", "desc": "随机入点偏移、素材顺序、插帧、随机转场", "deep": False},
    "audio": {"name": "音频差异化", "desc": "BGM 随机起播位置、轻微变速", "deep": False},
    "speed": {"name": "变速不变调", "desc": "整体速度 ±1-3%，画面与声音同步，观感几乎无感", "deep": True},
    "mirror": {"name": "水平镜像", "desc": "画面左右翻转（非对称画面适用）", "deep": True},
    "noise": {"name": "轻噪点", "desc": "叠加轻微胶片颗粒，打破画面指纹", "deep": True},
    "pitch": {"name": "音调微移", "desc": "声音整体升/降调 ≤3%，听感几乎无差", "deep": True},
}

DEDUPE_LEVELS: list[dict] = [
    {"value": "off", "label": "关闭"},
    {"value": "light", "label": "轻度"},
    {"value": "deep", "label": "深度"},
]

DEDUPE_LEVEL_HINT: dict[str, str] = {
    "off": "不做差异化，每条成片内容一致（适合单条投放）",
    "light": "画面与音频轻微扰动，成片观感基本不变（适合少量版本）",
    "deep": "片段级差异化（入点偏移/顺序/插帧/转场随机），每条结构不同（适合批量投放）",
}

DEEP_STRENGTHS: list[dict] = [
    {"value": "low", "label": "低", "hint": "±1% 扰动，观感几乎不变"},
    {"value": "medium", "label": "中", "hint": "±2% 扰动，推荐"},
    {"value": "high", "label": "高", "hint": "±3% 扰动，观感可察觉"},
]

COUNT_STEPS: list[int] = [10, 20, 50, 100, 200]


def options_payload() -> dict:
    """供 /api/options 下发的完整配置表（前端动态同步的唯一真相源）。"""
    return {
        "transitions": [{"value": k, "label": v} for k, v in TRANSITION_REGISTRY.items()],
        "dedupe_levels": DEDUPE_LEVELS,
        "dedupe_level_hint": DEDUPE_LEVEL_HINT,
        "dedupe_options": [{"key": k, "name": v["name"], "desc": v["desc"]} for k, v in DEDUPE_REGISTRY.items() if not v["deep"]],
        "deep_dedupe_options": [{"key": k, "name": v["name"], "desc": v["desc"]} for k, v in DEDUPE_REGISTRY.items() if v["deep"]],
        "deep_strengths": DEEP_STRENGTHS,
        "count_steps": COUNT_STEPS,
    }


DEFAULT_TRANSITIONS: list[str] = list(TRANSITION_REGISTRY.keys())


def pick_transition(config, index: int) -> Optional[str]:
    if config.transition_mode == "固定":
        return config.transition_type or "fade"
    if config.transition_mode == "随机":
        pool = config.transition_types or DEFAULT_TRANSITIONS
        # 过滤注册表外的无效值（前端动态表与后端不一致时兜底）
        pool = [t for t in pool if t in TRANSITION_REGISTRY]
        if not pool:
            return None
        rng = random.Random(config.random_seed + index)
        return rng.choice(pool)
    if config.use_transition:
        return config.transition_type or "fade"
    return None


def build_middle_pools(config) -> list[dict]:
    """标准化中间素材池列表：优先使用 middle_pools（多池），否则回退旧单池字段。

    每项结构：{"folder", "files", "items", "count"}
    - items：该池固定勾选的多条素材路径（按顺序插入）
    - count：未勾选时从该池随机抽取条数（None 表示兼容旧行为，默认抽 1 条）
    """
    pools: list[dict] = []
    if config.middle_pools:
        for pool in config.middle_pools:
            folder = str(pool.get("folder", "")).strip()
            if not folder:
                continue
            files = scan_videos(folder)
            items = [str(x) for x in pool.get("items", []) if str(x)][:10]
            count_raw = pool.get("count")
            count = None if count_raw is None else max(0, min(10, int(count_raw)))
            pools.append({"folder": folder, "files": files, "items": items, "count": count})
    elif config.middle_folder:
        files = scan_videos(config.middle_folder)
        items = [str(x) for x in (config.middle_items or []) if str(x)]
        if config.fixed_middle and not items:
            items = [config.fixed_middle]
        pools.append(
            {
                "folder": config.middle_folder,
                "files": files,
                "items": items,
                "count": config.middle_count,
            }
        )
    return pools


def pick_middle_sequence(
    pools: list[dict], index: int, random_seed: int, exclude: Optional[list[str]] = None
) -> list[str]:
    """按池顺序生成一条成片的中间片段序列：每池固定勾选优先，否则随机抽取 count 条。
    exclude：排除与开头/结尾/已选素材重复的文件（按路径与文件名）。"""
    seq: list[str] = []
    excluded = set()
    for p in (exclude or []):
        excluded.add(p)
        excluded.add(Path(p).name)
    for pi, pool in enumerate(pools):
        files = pool.get("files") or []
        if not files:
            continue
        items = [p for p in (pool.get("items") or []) if p in files]
        if items:
            # 固定勾选是用户明确指定，不做排除
            seq.extend(items)
            for p in items:
                excluded.add(p)
                excluded.add(Path(p).name)
            continue
        count = pool.get("count")
        if count is None:
            count = 1
        count = max(0, min(10, int(count)))
        if count <= 0:
            continue
        rng = random.Random(random_seed + index * 100 + pi)
        pool_files = [p for p in files if p not in excluded and Path(p).name not in excluded]
        if not pool_files:
            continue
        rng.shuffle(pool_files)
        picked = pool_files[: min(count, len(pool_files))]
        seq.extend(picked)
        for p in picked:
            excluded.add(p)
            excluded.add(Path(p).name)
    return seq


def middle_display(middle_items: list[str]) -> Optional[str]:
    if not middle_items:
        return None
    return "、".join(Path(p).name for p in middle_items)


def pick_bgm(config, index: int, bgm_files: list[str]) -> Optional[str]:
    if config.bgm_mode == "音乐文件夹固定":
        return config.fixed_bgm
    if config.bgm_mode == "音乐文件夹随机":
        if not bgm_files:
            return None
        rng = random.Random(config.random_seed + index)
        return rng.choice(bgm_files)
    if config.bgm_mode == "本地导入":
        return config.bgm_path
    return None


def render_output_name(
    template: str,
    index: int,
    head: str,
    tail: str,
    middle: Optional[str] = None,
) -> str:
    def clean(value: str) -> str:
        for ch in '\\/:*?"<>|':
            value = value.replace(ch, "_")
        return value.strip(" .")

    values = {
        "{序号}": f"{index:03d}",
        "{开头}": clean(Path(head).stem),
        "{结尾}": clean(Path(tail).stem),
        "{中间}": clean(Path(middle).stem) if middle else "无",
        "{日期}": time.strftime("%Y%m%d"),
        "{时间}": time.strftime("%H%M%S"),
        "{随机4位}": f"{random.randint(0, 9999):04d}",
    }
    raw = template or "output_{序号}_{开头}_{结尾}"
    had_seq = "{序号}" in raw  # 替换前判断是否含序号
    result = raw
    for key, value in values.items():
        result = result.replace(key, value)
    if "{" in result or "}" in result:
        # 模板含未识别变量（如被破坏的 {??} 或用户误写变量名）：回退默认模板，避免生成坏文件名
        raw = "output_{序号}_{开头}_{结尾}"
        had_seq = "{序号}" in raw
        result = raw
        for key, value in values.items():
            result = result.replace(key, value)
    if not had_seq:
        # 模板不含序号时自动追加序号（如 我的视频_001）：否则多条输出同名，
        # 会互相跳过只导出 1 条。追加后保证每条都导出且兼容断点续跑跳过逻辑。
        result = f"{result}_{index:03d}"
    return clean(result) + ".mp4"


def _unique_output_name(output_dir: Path, idx: int) -> str:
    stamp = time.strftime("%H%M%S")
    name = f"output_{idx:03d}_{stamp}.mp4"
    if not (output_dir / name).exists():
        return name
    n = 1
    while True:
        candidate = f"output_{idx:03d}_{stamp}_{n}.mp4"
        if not (output_dir / candidate).exists():
            return candidate
        n += 1


def resolve_duration_mode(mode: str) -> Optional[float]:
    mapping = {"15s": 15.0, "25s": 25.0, "30s": 30.0}
    return mapping.get(mode)


def resolve_resolution(value: str) -> tuple[int, int]:
    if value == "1080x1920":
        return 1080, 1920
    return 1920, 1080
