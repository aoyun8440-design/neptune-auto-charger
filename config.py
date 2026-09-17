"""Neptune 自动续充配置。"""

import os
from pathlib import Path

from dotenv import load_dotenv


env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)


def _read_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def _read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false")


# 用户认证信息。employeeId 可由 getUserInfo 自动获取，旧配置仍可选填并用于一致性校验。
OPEN_ID = os.getenv("NEPTUNE_OPEN_ID", "").strip()
AREA_ID = _read_int("NEPTUNE_AREA_ID", 6)
EMPLOYEE_ID = _read_int("NEPTUNE_EMPLOYEE_ID", 0)

# 默认只演练，不向充电桩发送开电请求。确认演练日志后再显式设为 false。
DRY_RUN = _read_bool("NEPTUNE_DRY_RUN", True)

# 最长续充时长。设备自己的上限更短时，以设备上限为准。
MAX_CHARGE_TIME = _read_int("NEPTUNE_MAX_CHARGE_TIME", 480)

# 充电平台已支持 HTTPS，避免 openId 与账户信息明文传输。
BASE_URL = "https://www.szlzxn.cn"

# 检测“昨天 23:45 至今天 00:30”的断电结束记录。
POWER_OFF_WINDOW_START_HOUR = 23
POWER_OFF_WINDOW_START_MINUTE = 45
POWER_OFF_WINDOW_END_HOUR = 0
POWER_OFF_WINDOW_END_MINUTE = 30
POWER_OFF_END_TYPE = 39


def validate_config() -> list[str]:
    """返回配置错误列表。"""
    errors: list[str] = []
    if not OPEN_ID:
        errors.append("NEPTUNE_OPEN_ID 未配置")
    if AREA_ID <= 0:
        errors.append("NEPTUNE_AREA_ID 必须大于 0")
    if not 1 <= MAX_CHARGE_TIME <= 24 * 60:
        errors.append("NEPTUNE_MAX_CHARGE_TIME 必须在 1 到 1440 分钟之间")
    return errors
