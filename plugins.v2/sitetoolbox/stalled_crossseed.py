"""识别长期未启动的辅种任务；不把暂停或零进度等同于文件缺失。"""
import math
import posixpath
import time


def number(torrent, key):
    try:
        value = float(torrent.get(key))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def content_key(torrent):
    path = torrent.get("content_path")
    if not path and torrent.get("save_path") and torrent.get("name"):
        path = posixpath.join(torrent["save_path"], torrent["name"])
    return posixpath.normpath(path) if path else None


def is_stalled_crossseed(torrent, all_torrents, required_tag="keep_iyuu", age_days=7, now=None):
    """所有证据齐全才列入候选；标签按完整项匹配，不使用分类兜底。"""
    required_tag = str(required_tag or "").strip()
    if not required_tag or "," in required_tag or "，" in required_tag:
        return False
    tags = {tag.strip() for tag in str(torrent.get("tags") or "").split(",")}
    if required_tag not in tags or torrent.get("state") not in {"pausedDL", "stoppedDL"}:
        return False
    if any(number(torrent, key) != 0 for key in ("progress", "completed", "downloaded", "uploaded", "seeding_time")):
        return False
    added = number(torrent, "added_on")
    active = number(torrent, "last_activity")
    completed = number(torrent, "completion_on")
    now = time.time() if now is None else now
    if added is None or added <= 0 or now - added < age_days * 86400:
        return False
    if active is None or active < 0 or active > added or completed is None or completed > 0:
        return False
    key = content_key(torrent)
    if not key:
        return False
    for other in all_torrents:
        if other.get("hash") == torrent.get("hash") or content_key(other) != key:
            continue
        if other.get("state") == "missingFiles":
            continue
        # 有正在使用同一数据的任务时，暂不认定为遗留辅种。
        if (other.get("state") not in {"pausedDL", "stoppedDL"}
                or number(other, "progress") != 0 or number(other, "completed") != 0):
            return False
    return True
