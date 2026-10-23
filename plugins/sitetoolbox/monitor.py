"""统一监控计划与按自然日确认异常的纯逻辑。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any


MODULES = {"rss": "RSS 检测", "userdata": "用户数据", "missing": "缺失种子扫描", "mteam": "馒头登录检查"}
BAD_STATES = {"error", "warning", "offline"}


def integer(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def boolean(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"} if isinstance(value, str) else bool(value)


def clock_minutes(value: Any) -> int:
    parts = str(value).split(":")
    if len(parts) != 2 or len(parts[0]) != 2 or len(parts[1]) != 2:
        raise ValueError("时间需要使用 HH:MM 格式")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("时间超出有效范围")
    return hour * 60 + minute


def normalize_plan(config: dict) -> dict:
    start, end = config.get("monitor_start", "08:00"), config.get("monitor_end", "23:00")
    try:
        clock_minutes(start)
        clock_minutes(end)
    except (ValueError, TypeError):
        start, end = "08:00", "23:00"
    times = config.get("monitor_times", ["08:00"])
    if isinstance(times, str):
        times = times.replace("，", ",").split(",")
    valid_times = []
    for value in times if isinstance(times, list) else []:
        try:
            clock_minutes(str(value).strip())
            valid_times.append(str(value).strip())
        except (ValueError, TypeError):
            continue
    modules = config.get("monitor_modules", list(MODULES))
    return {
        "monitor_enabled": boolean(config.get("monitor_enabled"), True),
        "monitor_modules": [key for key in MODULES if isinstance(modules, list) and key in modules],
        "monitor_start": start,
        "monitor_end": end,
        "monitor_mode": "fixed" if config.get("monitor_mode") == "fixed" else "interval",
        "monitor_interval": integer(config.get("monitor_interval"), 120, 5, 1440),
        "monitor_times": sorted(set(valid_times or ["08:00"])),
        "monitor_failure_days": integer(config.get("monitor_failure_days", config.get("rss_failure_days")), 3, 1, 365),
    }


def daily_slots(plan: dict, day: date) -> list[datetime]:
    start, end = clock_minutes(plan["monitor_start"]), clock_minutes(plan["monitor_end"])
    duration = (end - start) % 1440 or 1440
    midnight = datetime.combine(day, time())
    if plan["monitor_mode"] == "fixed":
        offsets = sorted({(clock_minutes(value) - start) % 1440 for value in plan["monitor_times"]})
        offsets = [offset for offset in offsets if offset <= duration]
    else:
        offsets = range(0, duration + (duration < 1440), plan["monitor_interval"])
    return [midnight + timedelta(minutes=start + offset) for offset in offsets]


def next_run(plan: dict, now: datetime) -> datetime | None:
    if not plan["monitor_enabled"] or not plan["monitor_modules"]:
        return None
    # 前一天的跨午夜时段可能仍覆盖今天。
    slots = [slot for offset in range(-1, 3) for slot in daily_slots(plan, now.date() + timedelta(days=offset))]
    return min((slot for slot in slots if slot > now), default=None)


def is_due(plan: dict, now: datetime) -> bool:
    if not plan["monitor_enabled"] or not plan["monitor_modules"]:
        return False
    minute = now.replace(second=0, microsecond=0)
    return any(minute in daily_slots(plan, now.date() + timedelta(days=offset)) for offset in (-1, 0))


def classify(result: dict, threshold: int) -> dict:
    result = dict(result)
    raw_state = result.get("raw_state", result.get("rss_state", result.get("state", "unknown")))
    raw_message = result.get("raw_message", result.get("rss_message", result.get("message", ""))) or ""
    days = integer(result.get("failure_days"), 1, 1, 1000000) if raw_state in BAD_STATES else 0
    confirmed = raw_state in BAD_STATES and days >= threshold
    state = raw_state
    if raw_state in {"error", "warning"} and not confirmed:
        state = "pending"
    message = raw_message
    if raw_state in BAD_STATES:
        prefix = f"已连续异常 {days} 天" if confirmed else f"连续异常 {days}/{threshold} 天，待观察"
        message = f"{prefix}：{raw_message}"
    result.update(state=state, message=message, raw_state=raw_state, raw_message=raw_message,
                  failure_days=days, failure_threshold=threshold, confirmed=confirmed)
    return result


def observe(history: dict, key: str, result: dict, threshold: int) -> tuple[dict, dict]:
    """同日失败去重；成功、漏测日期均中断连续累计。"""
    result = dict(result)
    tested_at = str(result.get("tested_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    today = date.fromisoformat(tested_at[:10])
    previous = history.get(key) if isinstance(history.get(key), dict) else {}
    try:
        previous_day = date.fromisoformat(str(previous.get("date") or ""))
    except ValueError:
        previous_day = None
    days = 0
    first_at = ""
    raw_state = result.get("raw_state", result.get("rss_state", result.get("state")))
    if raw_state in BAD_STATES:
        previous_days = integer(previous.get("days"), 0, 0, 1000000)
        if previous_day == today:
            days = max(1, previous_days)
        elif previous_day == today - timedelta(days=1):
            days = previous_days + 1
        else:
            days = 1
        first_at = previous.get("first_failed_at") if days > 1 or (previous_day == today and previous_days) else ""
        first_at = first_at or tested_at
    updated = {**history, key: {"date": today.isoformat(), "days": days, "first_failed_at": first_at}}
    result.update(tested_at=tested_at, failure_days=days, first_failed_at=first_at)
    return classify(result, threshold), updated
