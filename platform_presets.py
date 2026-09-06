PRESETS = {
    "抖音竖屏": {"resolution": "1080x1920", "duration_mode": "不限制"},
    "快手竖屏": {"resolution": "1080x1920", "duration_mode": "不限制"},
    "小红书竖屏": {"resolution": "1080x1920", "duration_mode": "不限制"},
    "B站横屏": {"resolution": "1920x1080", "duration_mode": "不限制"},
    "信息流15秒": {"resolution": "1080x1920", "duration_mode": "15s"},
    "信息流25秒": {"resolution": "1080x1920", "duration_mode": "25s"},
    "信息流30秒": {"resolution": "1080x1920", "duration_mode": "30s"},
}


def get_presets() -> dict:
    return dict(PRESETS)


def apply_preset(name: str) -> dict:
    return dict(PRESETS.get(name, {}))
