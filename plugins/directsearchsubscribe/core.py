"""直搜订阅的纯数据与匹配逻辑。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


TASK_SCHEMA_VERSION = 3
MAX_RESULTS = 50
MAX_RESOURCE_HISTORY = 500
PRIORITY_MODES = {"seeders", "balanced", "free", "latest", "smallest", "largest"}


def now_text() -> str:
    """返回插件统一使用的本地时间文本。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_bool(value: Any, default: bool = False) -> bool:
    """兼容表单、API 和旧配置中的布尔值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "开启", "启用"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "关闭", "停用"}:
        return False
    return default


def parse_int(value: Any, default: Optional[int] = None,
              minimum: Optional[int] = None, maximum: Optional[int] = None) -> Optional[int]:
    """解析带边界的整数。"""
    try:
        if value in (None, ""):
            return default
        number = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def parse_sites(value: Any) -> List[int]:
    """把站点多选值或文本解析成去重 ID 列表。"""
    if value in (None, "", []):
        return []
    values = re.split(r"[,，\s]+", value.strip()) if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    result = []
    for item in values:
        try:
            site_id = int(item)
        except (TypeError, ValueError):
            continue
        if site_id not in result:
            result.append(site_id)
    return result


def parse_lines(value: Any) -> List[str]:
    """解析每行一个的关键词，同时兼容列表。"""
    if value in (None, "", []):
        return []
    values = value if isinstance(value, (list, tuple, set)) else re.split(r"[\r\n]+", str(value))
    result = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def parse_words(value: Any) -> List[str]:
    """解析包含或排除词。"""
    if value in (None, "", []):
        return []
    values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,，\r\n]+", str(value))
    result = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def parse_episodes(value: Any) -> Set[int]:
    """解析 ``1-12,14`` 一类集数表达式。"""
    if value in (None, "", []):
        return set()
    values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,，\s]+", str(value))
    episodes: Set[int] = set()
    for item in values:
        part = str(item or "").strip()
        if not part:
            continue
        matched = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if matched:
            start, end = int(matched.group(1)), int(matched.group(2))
            start, end = min(start, end), max(start, end)
            if end - start <= 10000:
                episodes.update(range(start, end + 1))
        elif part.isdigit():
            episodes.add(int(part))
    return {episode for episode in episodes if episode > 0}


def episode_expression_is_valid(value: Any) -> bool:
    """校验集数表达式，避免悄悄忽略错误片段。"""
    if value in (None, "", []):
        return True
    values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,，\s]+", str(value))
    found = False
    for item in values:
        part = str(item or "").strip()
        if not part:
            continue
        found = True
        matched = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if matched:
            start, end = int(matched.group(1)), int(matched.group(2))
            if start <= 0 or end <= 0 or abs(end - start) > 10000:
                return False
        elif not part.isdigit() or int(part) <= 0:
            return False
    return found


def episodes_text(episodes: Iterable[int]) -> str:
    """把集数集合压缩为便于页面展示的范围文本。"""
    numbers = sorted({int(item) for item in episodes if int(item) > 0})
    if not numbers:
        return ""
    ranges = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def normalize_text(value: Any) -> str:
    """对中英文标题做保守归一化。"""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in text if char.isalnum())


def task_search_keywords(task: Dict[str, Any]) -> List[str]:
    """返回实际发送到站点的搜索词。"""
    keywords = parse_lines(task.get("keywords"))
    if not keywords:
        keywords = [str(task.get("name") or "").strip()]
    return [item for item in keywords if item]


def task_title_candidates(task: Dict[str, Any]) -> List[str]:
    """返回用于严格标题匹配的人工标题集合。"""
    values = [task.get("name"), *parse_lines(task.get("aliases")), *task_search_keywords(task)]
    result = []
    normalized_results = set()
    for value in values:
        text = str(value or "").strip()
        normalized = normalize_text(text)
        if text and len(normalized) >= 2 and normalized not in normalized_results:
            result.append(text)
            normalized_results.add(normalized)
    return result


def title_matches(task: Dict[str, Any], title: str, description: str = "") -> bool:
    """判断站点结果是否命中人工标题或别名。"""
    if not parse_bool(task.get("strict_title_match"), True):
        return True
    haystack = normalize_text(f"{title} {description}")
    return any(normalize_text(candidate) in haystack for candidate in task_title_candidates(task))


def words_match(task: Dict[str, Any], title: str, description: str = "") -> bool:
    """应用必须包含和排除词。"""
    text = f"{title} {description}".casefold()
    includes = [word.casefold() for word in parse_words(task.get("include"))]
    excludes = [word.casefold() for word in parse_words(task.get("exclude"))]
    return (not includes or all(word in text for word in includes)) \
        and not any(word in text for word in excludes)


def target_episodes(task: Dict[str, Any]) -> Set[int]:
    """计算任务的有限目标集；空集合表示持续追更模式。"""
    explicit = parse_episodes(task.get("episodes"))
    if explicit:
        return explicit
    total = parse_int(task.get("total_episode"), minimum=1)
    if not total:
        return set()
    start = parse_int(task.get("start_episode"), 1, minimum=1) or 1
    return set(range(start, total + 1)) if start <= total else set()


def missing_episodes(task: Dict[str, Any]) -> Set[int]:
    """返回有限目标中尚未成功添加下载的集数。"""
    return target_episodes(task).difference(parse_episodes(task.get("downloaded_episodes")))


def resource_fingerprint(site_id: Any, enclosure: Any, page_url: Any,
                         title: Any, size: Any) -> str:
    """生成稳定资源指纹，防止定时任务重复添加同一种子。"""
    raw = "\n".join([
        str(site_id or ""),
        str(page_url or enclosure or ""),
        str(title or ""),
        str(size or 0),
    ])
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def resource_identity(title: Any) -> str:
    """生成跨站点资源标识；相同发布标题只允许下载一次。"""
    normalized = normalize_text(title)
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest() \
        if normalized else ""


def normalize_priority_mode(value: Any) -> str:
    """归一化候选资源优先规则。"""
    mode = str(value or "seeders").strip().lower()
    aliases = {
        "综合": "balanced", "做种": "seeders", "做种数": "seeders",
        "免费": "free", "最新": "latest", "小体积": "smallest", "大体积": "largest",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in PRIORITY_MODES else "seeders"


def _pubdate_rank(value: Any) -> int:
    """把常见发布时间转换为可排序整数，无法识别时返回 0。"""
    if isinstance(value, datetime):
        return int(value.strftime("%Y%m%d%H%M%S"))
    digits = re.sub(r"\D", "", str(value or ""))[:14]
    return int(digits.ljust(14, "0")) if len(digits) >= 8 else 0


def candidate_sort_key(priority_mode: Any, episodes: Set[int], missing: Set[int],
                       seeders: int, free_factor: Optional[float], size: int,
                       pubdate: Any = None) -> Tuple[int, ...]:
    """生成候选排序键；缺集覆盖和可识别集数始终优先，再应用用户规则。"""
    covered = len(episodes.intersection(missing)) if missing else len(episodes)
    known = 1 if episodes else 0
    seeds = max(int(seeders or 0), 0)
    free = 1 if free_factor == 0 else 0
    bytes_size = max(int(size or 0), 0)
    latest = _pubdate_rank(pubdate)
    mode = normalize_priority_mode(priority_mode)
    priorities = {
        "seeders": (seeds, free, latest, -bytes_size),
        "balanced": (free, seeds, latest, -bytes_size),
        "free": (free, seeds, latest, -bytes_size),
        "latest": (latest, seeds, free, -bytes_size),
        "smallest": (-bytes_size, seeds, free, latest),
        "largest": (bytes_size, seeds, free, latest),
    }
    return covered, known, *priorities[mode]


def is_duplicate_download_message(value: Any) -> bool:
    """识别下载器返回的“任务已存在”结果。"""
    text = str(value or "").casefold()
    return any(marker in text for marker in ("已存在", "already exists", "duplicate"))


def candidate_score(episodes: Set[int], missing: Set[int], seeders: int,
                    free_factor: Optional[float], size: int) -> int:
    """按缺集覆盖、可识别性、促销和做种数计算候选分数。"""
    covered = len(episodes.intersection(missing)) if missing else len(episodes)
    known_bonus = 1 if episodes else 0
    free_bonus = 1 if free_factor == 0 else 0
    size_bonus = min(max(int(size or 0) // (1024 ** 3), 0), 999)
    return covered * 1_000_000_000 + known_bonus * 100_000_000 \
        + free_bonus * 10_000_000 + max(int(seeders or 0), 0) * 1_000 + size_bonus


def normalize_task(payload: Dict[str, Any], existing: Optional[Dict[str, Any]] = None,
                   task_id: Optional[str] = None) -> Dict[str, Any]:
    """把配置表单或 API 数据归一化为插件任务。"""
    source = dict(existing or {})
    source.update(payload or {})
    name = str(source.get("name") or source.get("title") or "").strip()
    media_type = str(source.get("type") or "电视剧").strip().lower()
    media_type = "电影" if media_type in {"movie", "电影", "m"} else "电视剧"
    created_at = str((existing or {}).get("created_at") or now_text())
    normalized_id = str((existing or {}).get("id") or task_id or uuid.uuid4().hex[:12])
    episodes = episodes_text(parse_episodes(source.get("episodes")))
    owned = parse_episodes(source.get("owned_episodes"))
    downloaded = parse_episodes(source.get("downloaded_episodes"))
    downloaded.update(owned)
    task = {
        "schema_version": TASK_SCHEMA_VERSION,
        "id": normalized_id,
        "name": name,
        "type": media_type,
        "year": str(source.get("year") or "").strip(),
        "season": parse_int(source.get("season"), minimum=1),
        "episodes": episodes,
        "start_episode": parse_int(source.get("start_episode"), 1, minimum=1) or 1,
        "total_episode": parse_int(source.get("total_episode"), minimum=1),
        "keywords": parse_lines(source.get("keywords") or source.get("keyword") or name),
        "aliases": parse_lines(source.get("aliases")),
        "include": parse_words(source.get("include")),
        "exclude": parse_words(source.get("exclude")),
        "sites": parse_sites(source.get("sites")),
        "search_pages": parse_int(source.get("search_pages"), 1, minimum=1, maximum=5) or 1,
        "downloader": str(source.get("downloader") or "").strip(),
        "save_path": str(source.get("save_path") or "").strip(),
        "media_category": str(source.get("media_category") or "").strip(),
        "enabled": parse_bool(source.get("enabled"), True),
        "auto_download": parse_bool(source.get("auto_download"), False),
        "strict_title_match": parse_bool(source.get("strict_title_match"), True),
        "accept_unknown_episode": parse_bool(source.get("accept_unknown_episode"), False),
        "dedupe_history": parse_bool(source.get("dedupe_history"), True),
        "priority_mode": normalize_priority_mode(source.get("priority_mode")),
        "min_seeders": parse_int(source.get("min_seeders"), 0, minimum=0, maximum=1000000) or 0,
        "owned_episodes": episodes_text(owned),
        "downloaded_episodes": sorted(downloaded),
        "downloaded_fingerprints": list(dict.fromkeys(source.get("downloaded_fingerprints") or []))[-MAX_RESOURCE_HISTORY:],
        "download_records": list(source.get("download_records") or [])[-MAX_RESOURCE_HISTORY:],
        "last_results": list(source.get("last_results") or [])[:MAX_RESULTS],
        "status": str(source.get("status") or "active"),
        "last_run_at": str(source.get("last_run_at") or ""),
        "last_status": str(source.get("last_status") or ""),
        "last_message": str(source.get("last_message") or ""),
        "last_match_count": parse_int(source.get("last_match_count"), 0, minimum=0) or 0,
        "last_download_count": parse_int(source.get("last_download_count"), 0, minimum=0) or 0,
        "last_duplicate_count": parse_int(source.get("last_duplicate_count"), 0, minimum=0) or 0,
        "created_at": created_at,
        "updated_at": now_text(),
    }
    target = target_episodes(task)
    if target and target.issubset(set(task["downloaded_episodes"])):
        task["status"] = "completed"
    elif not task["enabled"]:
        task["status"] = "paused"
    elif task["status"] in {"completed", "paused", "error"}:
        task["status"] = "active"
    return task


def validate_task(task: Dict[str, Any], raw_payload: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """校验任务创建所需的最小字段。"""
    if not str(task.get("name") or "").strip():
        return "节目名称不能为空"
    if not task_search_keywords(task):
        return "至少需要一个站点搜索关键词"
    if task.get("type") == "电视剧":
        source_episodes = (raw_payload or {}).get("episodes", task.get("episodes"))
        if not episode_expression_is_valid(source_episodes):
            return "指定集数格式无效，请使用 1-12,14"
        total = parse_int(task.get("total_episode"), minimum=1)
        start = parse_int(task.get("start_episode"), 1, minimum=1) or 1
        if total and start > total and not parse_episodes(task.get("episodes")):
            return "起始集不能大于总集数"
    return None
