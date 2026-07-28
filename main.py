"""Neptune 断电后自动续充。

默认处于演练模式，只检查账户、历史记录、设备和端口，不发送 beginCharge。
目前只支持平台 ``measure == 1`` 的按电量计费模式；其他模式会安全退出。
"""

import aiohttp
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional, Tuple

from config import (
    AREA_ID,
    BASE_URL,
    DRY_RUN,
    EMPLOYEE_ID,
    MAX_CHARGE_TIME,
    OPEN_ID,
    POWER_OFF_END_TYPE,
    POWER_OFF_WINDOW_END_HOUR,
    POWER_OFF_WINDOW_END_MINUTE,
    POWER_OFF_WINDOW_START_HOUR,
    POWER_OFF_WINDOW_START_MINUTE,
    validate_config,
)


MAX_RETRIES = 4
RETRY_INTERVAL = 10 * 60
CONFIRM_ATTEMPTS = 16
CONFIRM_INTERVAL = 6
MIN_METER_BALANCE_CENTS = 200

TZ_BEIJING = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
        "Chrome/142.0 Mobile Safari/537.36 MicroMessenger/8.0"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/wx/indexn.html?openId={OPEN_ID}&areaid={AREA_ID}",
    "Accept": "*/*",
}


class ChargeResult(Enum):
    SUCCESS = "success"
    DRY_RUN = "dry_run"
    NO_RECORD = "no_record"
    PORT_BUSY = "port_busy"
    ERROR = "error"


class ApiError(RuntimeError):
    """平台接口返回异常或业务失败。"""


class UnsupportedChargeMode(RuntimeError):
    """设备计费模式尚未经过验证，禁止猜测参数开电。"""


def log(message: str) -> None:
    now = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


async def post_json(
    session: aiohttp.ClientSession,
    path: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    async with session.post(f"{BASE_URL}{path}", data=data, headers=HEADERS) as resp:
        resp.raise_for_status()
        result = await resp.json(content_type=None)
    if not isinstance(result, dict):
        raise ApiError(f"{path} 返回格式异常")
    return result


async def get_user_info(session: aiohttp.ClientSession) -> dict[str, Any]:
    result = await post_json(
        session,
        "/wxn/getUserInfo",
        {"openId": OPEN_ID, "areaId": AREA_ID},
    )
    if not result.get("success") or not isinstance(result.get("obj"), dict):
        raise ApiError(f"获取用户信息失败: {result.get('msg') or '未知错误'}")
    return result["obj"]


async def get_charge_log(
    session: aiohttp.ClientSession,
    employee_id: int,
    term: str,
) -> list[dict[str, Any]]:
    result = await post_json(
        session,
        "/wxn/getChargeLog",
        {"employeeid": employee_id, "term": term},
    )
    if not result.get("success"):
        raise ApiError(f"获取充电历史失败: {result.get('msg') or '未知错误'}")
    records = result.get("obj", [])
    if not isinstance(records, list):
        raise ApiError("充电历史返回格式异常")
    return [record for record in records if isinstance(record, dict)]


async def get_device_info(
    session: aiohttp.ClientSession,
    devaddress: str,
) -> dict[str, Any]:
    result = await post_json(
        session,
        "/wxn/getDeviceInfo",
        {"areaId": AREA_ID, "devaddress": devaddress},
    )
    if not result.get("success") or not isinstance(result.get("obj"), dict):
        raise ApiError(f"获取设备信息失败: {result.get('msg') or '未知错误'}")
    return result["obj"]


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise UnsupportedChargeMode(f"设备缺少有效的 {field}") from exc


def build_charge_params(
    devaddress: str,
    port: str,
    balance_cents: int,
    device_info: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """按照平台现行前端的按电量计费协议构造开电参数。

    ``money=7`` 表示按电量计费；``yuan7`` 为时长分钟数，0 表示使用设备
    默认“充满自停”时长。其他计费模式的字段语义不同，不能复用。
    """
    measure = _as_int(device_info.get("measure"), "measure")
    if measure != 1:
        raise UnsupportedChargeMode(
            f"暂不支持该设备计费模式 measure={measure}；已禁止发送开电请求"
        )

    device_max_minutes = _as_int(device_info.get("ycTime"), "ycTime")
    if device_max_minutes <= 0:
        raise UnsupportedChargeMode("设备 ycTime 必须大于 0")

    duration_minutes = min(MAX_CHARGE_TIME, device_max_minutes)
    yuan7 = 0 if duration_minutes == device_max_minutes else duration_minutes

    params: dict[str, Any] = {
        "devaddress": devaddress,
        "port": port,
        "money": 7,
        "areaId": AREA_ID,
        "openId": OPEN_ID,
        "beforemoney": balance_cents,
        "devtypeid": device_info.get("devtypeid", 40),
        "fullStop": 0,
        "payType": 1,
        "safeOpen": 0,
        "safeCharge": device_info.get("safeCharge", 0),
        "edtType": 0,
        "efee": device_info.get("efee"),
        "eCharge": device_info.get("eCharge"),
        "serviceCharge": device_info.get("serviceCharge"),
        "userId": 0,
        "yuan7": yuan7,
    }
    return params, duration_minutes


async def begin_charge(
    session: aiohttp.ClientSession,
    params: dict[str, Any],
) -> dict[str, Any]:
    """发送开电请求，并按平台网页相同节奏轮询确认结果。"""
    result = await post_json(session, "/wxn/beginCharge", params)
    if not result.get("success"):
        return {"success": False, "msg": f"开电请求失败: {result.get('msg')}"}

    msgflag = result.get("obj")
    if not msgflag:
        return {"success": False, "msg": "开电请求未返回 msgflag"}

    confirm_params = {**params, "msgflag": msgflag}
    last_result: dict[str, Any] = {"success": False, "msg": "设备确认超时"}
    for attempt in range(1, CONFIRM_ATTEMPTS + 1):
        await asyncio.sleep(CONFIRM_INTERVAL)
        last_result = await post_json(session, "/wxn/beginCharge", confirm_params)
        if last_result.get("success"):
            return last_result
        log(
            f"设备确认第 {attempt}/{CONFIRM_ATTEMPTS} 次未成功: "
            f"{last_result.get('msg') or '暂无响应'}"
        )
    return last_result


def find_power_off_record(
    logs: list[dict[str, Any]],
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """返回昨夜断电窗口内时间最新的一条 endtype=39 记录。"""
    current = now or datetime.now(TZ_BEIJING)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ_BEIJING)
    else:
        current = current.astimezone(TZ_BEIJING)

    today = current.date()
    yesterday = today - timedelta(days=1)
    window_start = datetime(
        yesterday.year,
        yesterday.month,
        yesterday.day,
        POWER_OFF_WINDOW_START_HOUR,
        POWER_OFF_WINDOW_START_MINUTE,
        tzinfo=TZ_BEIJING,
    )
    window_end = datetime(
        today.year,
        today.month,
        today.day,
        POWER_OFF_WINDOW_END_HOUR,
        POWER_OFF_WINDOW_END_MINUTE,
        tzinfo=TZ_BEIJING,
    )
    log(
        "检测时间窗口: "
        f"{window_start.strftime('%Y-%m-%d %H:%M')} ~ "
        f"{window_end.strftime('%Y-%m-%d %H:%M')}"
    )

    candidates: list[tuple[int, dict[str, Any]]] = []
    for record in logs:
        try:
            endtype = int(record.get("endtype"))
            enddt = int(record.get("enddt"))
        except (TypeError, ValueError):
            continue
        if endtype != POWER_OFF_END_TYPE:
            continue
        end_time = datetime.fromtimestamp(enddt / 1000, tz=TZ_BEIJING)
        if window_start <= end_time <= window_end:
            candidates.append((enddt, record))

    if not candidates:
        return None

    enddt, record = max(candidates, key=lambda candidate: candidate[0])
    end_time = datetime.fromtimestamp(enddt / 1000, tz=TZ_BEIJING)
    log(
        "找到符合条件的断电记录: "
        f"结束时间={end_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return record


def is_port_free(portstatur: str, port: str) -> bool:
    try:
        port_index = int(port)
        return 0 <= port_index < len(portstatur) and portstatur[port_index] == "0"
    except (TypeError, ValueError, IndexError):
        return False


async def try_charge(session: aiohttp.ClientSession) -> Tuple[ChargeResult, str]:
    try:
        log("获取用户信息...")
        user_info = await get_user_info(session)
        employee_id = _as_int(user_info.get("employeeid"), "employeeid")
        if EMPLOYEE_ID and EMPLOYEE_ID != employee_id:
            return ChargeResult.ERROR, (
                "NEPTUNE_EMPLOYEE_ID 与 openId 所属用户不一致；"
                "请删除旧值或重新核对"
            )

        balance = _as_int(user_info.get("readyaccountmoney", 0), "readyaccountmoney")
        if balance < MIN_METER_BALANCE_CENTS:
            return ChargeResult.ERROR, "按电量计费当前至少需要 2 元账户余额"
        log("账户余额满足按电量计费最低要求")

        now = datetime.now(TZ_BEIJING)
        logs: list[dict[str, Any]] = []
        term_this = now.strftime("%Y%m")
        log(f"获取 {term_this} 充电历史...")
        logs.extend(await get_charge_log(session, employee_id, term_this))
        if now.day <= 3:
            last_month = now.replace(day=1) - timedelta(days=1)
            term_last = last_month.strftime("%Y%m")
            log(f"获取 {term_last} 充电历史...")
            logs.extend(await get_charge_log(session, employee_id, term_last))

        if not logs:
            return ChargeResult.NO_RECORD, "无充电历史记录"
        log(f"共获取 {len(logs)} 条充电记录")

        log("检查断电记录...")
        record = find_power_off_record(logs, now=now)
        if not record:
            return ChargeResult.NO_RECORD, "未找到符合条件的断电记录"

        devaddress_value = record.get("devaddress")
        port_value = record.get("devport")
        devaddress = "" if devaddress_value is None else str(devaddress_value).strip()
        port = "" if port_value is None else str(port_value).strip()
        if not devaddress or not port:
            return ChargeResult.ERROR, "断电记录缺少设备或端口信息"

        log("获取断电记录对应的设备信息...")
        device_info = await get_device_info(session, devaddress)
        portstatur = str(device_info.get("portstatur") or "")
        port_is_free = is_port_free(portstatur, port)
        log(f"端口状态检查: {'空闲' if port_is_free else '非空闲'}")
        if not port_is_free:
            return ChargeResult.PORT_BUSY, "断电记录对应端口非空闲（可能尚未供电）"

        params, duration_minutes = build_charge_params(
            devaddress,
            port,
            balance,
            device_info,
        )
        log(
            "续充计划: 使用断电记录中的同一设备和端口, "
            f"最长={duration_minutes} 分钟, 按电量计费, 余额支付"
        )

        if DRY_RUN:
            return ChargeResult.DRY_RUN, "演练检查通过，未发送开电请求"

        result = await begin_charge(session, params)
        if result.get("success"):
            return ChargeResult.SUCCESS, f"最长={duration_minutes} 分钟"
        return ChargeResult.ERROR, f"充电启动失败: {result.get('msg') or '未知错误'}"

    except (aiohttp.ClientError, asyncio.TimeoutError, ApiError) as exc:
        return ChargeResult.ERROR, f"平台请求异常: {exc}"
    except UnsupportedChargeMode as exc:
        return ChargeResult.ERROR, str(exc)
    except Exception as exc:  # 最后一层保护，避免工作流假绿。
        return ChargeResult.ERROR, f"发生异常: {type(exc).__name__}: {exc}"


async def async_main() -> int:
    log("=" * 50)
    log("Neptune 自动续充脚本启动")
    log(f"运行模式: {'演练（不会开电）' if DRY_RUN else '真实充电'}")
    log(f"重试策略: 最多 {MAX_RETRIES} 次，间隔 {RETRY_INTERVAL // 60} 分钟")
    log("=" * 50)

    config_errors = validate_config()
    if config_errors:
        for error in config_errors:
            log(f"配置错误: {error}")
        return 2

    timeout = aiohttp.ClientTimeout(total=30)
    for attempt in range(1, MAX_RETRIES + 1):
        log(f"\n--- 第 {attempt}/{MAX_RETRIES} 次尝试 ---")
        async with aiohttp.ClientSession(timeout=timeout) as session:
            result, message = await try_charge(session)

        if result == ChargeResult.SUCCESS:
            log("=" * 50)
            log("充电启动成功！")
            log(f"  {message}")
            log("=" * 50)
            return 0
        if result == ChargeResult.DRY_RUN:
            log("=" * 50)
            log(message)
            log("=" * 50)
            return 0
        if result == ChargeResult.NO_RECORD:
            log(f"结果: {message}")
            log("无需恢复充电，退出")
            return 0

        log(f"结果: {message}")
        if attempt < MAX_RETRIES:
            log(f"将在 {RETRY_INTERVAL // 60} 分钟后重试...")
            await asyncio.sleep(RETRY_INTERVAL)

    log("所有重试均失败")
    return 1


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(asyncio.run(async_main()))
    except ValueError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        sys.exit(2)
