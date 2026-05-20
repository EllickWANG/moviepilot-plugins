import copy
import hashlib
import inspect
import json
import math
import os
import random
import re
import tempfile
import time
import traceback
from datetime import timedelta, datetime
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional
from threading import Event, RLock
import iso639
import srt
from lxml import etree
from dataclasses import dataclass
from enum import Enum
import queue
import threading
from uuid import uuid4
from apscheduler.triggers.cron import CronTrigger
import httpx
from app import schemas
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event as MPEvent
from app.schemas import TransferInfo
from app.schemas.types import NotificationType, EventType
from app.log import logger
from app.plugins import _PluginBase
from .ffmpeg import Ffmpeg
from .translate.openai_translate import OpenAi


class UserInterruptException(Exception):
    """用户中断当前任务的异常"""
    pass


class AsrTransientException(Exception):
    """远程ASR临时网络异常，可等待后重试任务"""
    pass


class AsrRequestError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def _log_preview(value: Any, limit: int = 6000) -> str:
    try:
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        value = str(value)
    value = value.replace("\r", "\\r")
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...<truncated {len(value) - limit} chars>"


class TaskSource(Enum):
    MANUAL = "manual"
    EVENT = "event"
    AUTO_SCAN = "auto_scan"


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_FILE = "waiting_file"
    COMPLETED = "completed"
    IGNORED = "ignored"
    FAILED = "failed"


@dataclass
class TaskItem:
    task_id: str
    video_file: str
    source: TaskSource
    add_time: datetime
    status: TaskStatus = TaskStatus.PENDING
    complete_time: datetime = None
    progress: float = 0.0
    progress_stage: str = "等待中"
    progress_detail: str = ""
    progress_updated: datetime = None
    integrity_retry_count: int = 0
    next_retry_time: datetime = None


class AutoSubRemoteAsr(_PluginBase):
    # 插件名称
    plugin_name = "AI字幕ASR"
    # 插件描述
    plugin_desc = "使用远程语音识别接口生成字幕，并通过当前插件接口配置翻译中文字幕。"
    # 插件图标
    plugin_icon = "mdi-subtitles-outline"
    # 主题色
    plugin_color = "#2C4F7E"
    # 插件版本
    plugin_version = "1.0.47"
    # 插件作者
    plugin_author = "Ellick"
    # 作者主页
    author_url = "https://github.com/EllickWANG"
    # 插件配置项ID前缀
    plugin_config_prefix = "autosubremoteasr"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _tasks: Dict[str, TaskItem] = None
    _task_queue = None
    _consumer_thread = None
    _consumer_threads: Dict[int, threading.Thread] = None
    _current_processing_task = None
    _current_processing_tasks: Dict[str, TaskItem] = None
    _queued_task_ids = None
    _tasks_lock = None
    _progress_save_at: Dict[str, float] = None
    _scheduled_retry_tasks: Dict[str, float] = None
    _auto_scan_thread = None
    _auto_scan_lock = None
    _translate_lock = None
    _parallel_tasks = 1
    _translate_concurrency = 1
    _cpu_threads = 1
    _full_integrity_check = False
    _asr_api_model = "whisper-1"
    _asr_chunk_minutes = 10
    _asr_chunk_seconds = 600
    _asr_request_timeout = 300
    _asr_prompt = ""
    _translate_request_timeout = 120
    _asr_random_check_rate = 0.2
    _asr_request_retries = 3
    _asr_retry_delays = (10, 30, 60)
    _reuse_autosub_config = False
    _openai_api_key = None
    _openai_api_url = None
    _openai_api_proxy = False
    _openai_api_compatible = False
    _openai_model = None
    _integrity_retry_interval = 10 * 60
    _auto_scan_enabled = True
    _auto_scan_cron = "*/10 * * * *"
    _running = False
    _event = Event()
    _enabled = None
    _reset_tasks = None
    _listen_transfer_event = None
    _send_notify = None
    _detailed_log = False
    _retry_failed_once = None
    _translate_preference = None
    _run_now = None
    _path_list = None
    _exclude_path_list = None
    _translate_zh = None
    _openai = None
    _enable_batch = None
    _batch_size = None
    _context_window = None
    _max_retries = None
    _enable_merge = None
    _enable_asr = None
    _auto_detect_language = None

    def __ensure_runtime_state(self):
        if self._tasks_lock is None:
            self._tasks_lock = RLock()
        if self._consumer_threads is None:
            self._consumer_threads = {}
        if self._current_processing_tasks is None:
            self._current_processing_tasks = {}
        if self._queued_task_ids is None:
            self._queued_task_ids = set()
        if self._progress_save_at is None:
            self._progress_save_at = {}
        if self._scheduled_retry_tasks is None:
            self._scheduled_retry_tasks = {}
        if self._auto_scan_lock is None:
            self._auto_scan_lock = threading.Lock()
        if self._translate_lock is None:
            self._translate_lock = threading.Semaphore(1)

    @staticmethod
    def __normalize_parallel_tasks(value) -> int:
        try:
            return max(1, int(value or 1))
        except Exception:
            return 1

    @staticmethod
    def __normalize_translate_concurrency(value) -> int:
        try:
            return max(1, int(value or 1))
        except Exception:
            return 1

    @staticmethod
    def __normalize_retry_minutes(value) -> int:
        try:
            return max(5, min(1440, int(value or 10)))
        except Exception:
            return 10

    @staticmethod
    def __normalize_batch_size(value) -> int:
        try:
            return max(1, min(50, int(value or 10)))
        except Exception:
            return 10

    @staticmethod
    def __default_asr_prompt() -> str:
        return (
            "请按原音语言转写为字幕文本，不翻译、不总结、不改写；"
            "保留重复、停顿、语气词、拟声词和人名术语。"
        )

    @staticmethod
    def __normalize_asr_chunk_minutes(value) -> int:
        try:
            return max(5, min(30, int(value or 10)))
        except Exception:
            return 10

    @staticmethod
    def __normalize_request_timeout(value, default: int) -> int:
        try:
            return max(10, min(1800, int(value or default)))
        except Exception:
            return default

    @staticmethod
    def __normalize_path_list(value) -> List[str]:
        if not value:
            return []
        if isinstance(value, list):
            paths = value
        else:
            paths = str(value).split("\n")
        return list(dict.fromkeys([path.strip() for path in paths if path and str(path).strip()]))

    @staticmethod
    def __path_matches_excludes(path: str, exclude_paths: Optional[List[str]]) -> bool:
        if not path or not exclude_paths:
            return False
        path_text = str(path)
        try:
            abs_path = os.path.abspath(path_text)
        except Exception:
            abs_path = path_text
        for rule in exclude_paths:
            rule_text = str(rule or "").strip()
            if not rule_text:
                continue
            if os.path.isabs(rule_text):
                abs_rule = os.path.abspath(rule_text).rstrip(os.sep)
                if abs_path == abs_rule or abs_path.startswith(f"{abs_rule}{os.sep}"):
                    return True
            elif rule_text in path_text:
                return True
        return False

    def __is_excluded_path(self, path: str) -> bool:
        return self.__path_matches_excludes(path, self._exclude_path_list)

    @staticmethod
    def __pick_first_key(key_value: Optional[str]) -> Optional[str]:
        if not key_value:
            return None
        return [key.strip() for key in str(key_value).split(",") if key.strip()][0]

    def __extract_openai_settings(self, source_config: Optional[dict], source_name: str) -> Optional[dict]:
        if not source_config:
            return None

        if source_config.get("use_chatgpt"):
            chatgpt = self.get_config("ChatGPT")
            if not chatgpt:
                logger.warn(f"{source_name} 配置为复用ChatGPT，但未读取到ChatGPT插件配置")
                return None
            openai_key = self.__pick_first_key(chatgpt.get("openai_key"))
            if not openai_key:
                logger.warn(f"{source_name} 复用的ChatGPT配置缺少openai_key")
                return None
            return {
                "key": openai_key,
                "url": chatgpt.get("openai_url") or "https://api.openai.com",
                "proxy": bool(chatgpt.get("proxy", False)),
                "model": chatgpt.get("model") or "gpt-3.5-turbo",
                "compatible": bool(chatgpt.get("compatible", False)),
                "source": f"{source_name}/ChatGPT",
            }

        openai_key = self.__pick_first_key(source_config.get("openai_key"))
        if not openai_key:
            return None
        return {
            "key": openai_key,
            "url": source_config.get("openai_url") or "https://api.openai.com",
            "proxy": bool(source_config.get("openai_proxy", False)),
            "model": source_config.get("openai_model") or "gpt-3.5-turbo",
            "compatible": False if source_name == "当前插件" else bool(source_config.get("compatible", False)),
            "source": source_name,
        }

    def __resolve_openai_settings(self, config: dict) -> bool:
        self._openai_api_key = None
        self._openai_api_url = None
        self._openai_api_proxy = False
        self._openai_api_compatible = False
        self._openai_model = None

        sources = [(config, "当前插件")]
        if self._reuse_autosub_config:
            sources.append((self.get_config("AutoSubv2Plus"), "AI字幕自动生成(v2) 私有版历史配置"))

        for source_config, source_name in sources:
            settings_data = self.__extract_openai_settings(source_config, source_name)
            if not settings_data:
                continue
            self._openai_api_key = settings_data["key"]
            self._openai_api_url = settings_data["url"]
            self._openai_api_proxy = settings_data["proxy"]
            self._openai_api_compatible = settings_data["compatible"]
            self._openai_model = settings_data["model"]
            logger.info(f"已使用 {settings_data['source']} 的大模型接口配置")
            return True
        return False

    def __get_openai_base_url(self) -> str:
        base_url = self._openai_api_url.rstrip("/") if self._openai_api_url else "https://api.openai.com"
        if not self._openai_api_compatible and not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        return base_url

    def __create_openai_http_client(self, timeout: Optional[int] = None) -> httpx.Client:
        proxy_url = None
        if self._openai_api_proxy:
            proxy_config = settings.PROXY or {}
            proxy_url = proxy_config.get("https") or proxy_config.get("http")
        client_kwargs = {"timeout": timeout or self._asr_request_timeout}
        if proxy_url:
            httpx_client_params = inspect.signature(httpx.Client).parameters
            if "proxy" in httpx_client_params:
                client_kwargs["proxy"] = proxy_url
            elif "proxies" in httpx_client_params:
                client_kwargs["proxies"] = proxy_url
        return httpx.Client(**client_kwargs)

    def init_plugin(self, config=None):
        # 如果没有配置信息， 则不处理
        if not config:
            return
        config = dict(config)
        removed_keys = []
        for key in ("reuse_autosub_config", "compatible", "use_chatgpt", "cpu_threads"):
            if key in config:
                config.pop(key, None)
                removed_keys.append(key)
        if removed_keys:
            logger.info(f"已清理接口ASR旧配置项：{', '.join(removed_keys)}")
            self.update_config(config)
        self.__ensure_runtime_state()
        self._tasks = self.load_tasks()
        self._progress_save_at = {}
        self._enabled = config.get('enabled', False)
        self._reset_tasks = bool(config.get('reset_tasks', False) or config.get('clear_history', False))
        self._listen_transfer_event = config.get('listen_transfer_event', True)
        self._run_now = config.get('run_now')
        self._retry_failed_once = config.get('retry_failed_once')
        self._path_list = self.__normalize_path_list(config.get('path_list'))
        self._exclude_path_list = self.__normalize_path_list(config.get('exclude_path_list'))
        self._send_notify = config.get('send_notify', False)
        self._detailed_log = bool(config.get('detailed_log', False))
        self._parallel_tasks = self.__normalize_parallel_tasks(config.get('parallel_tasks', 1))
        self._translate_concurrency = self.__normalize_translate_concurrency(config.get('translate_concurrency', 1))
        self._translate_lock = threading.Semaphore(self._translate_concurrency)
        self._cpu_threads = 1
        self._full_integrity_check = bool(config.get('full_integrity_check', False))
        self._auto_scan_enabled = bool(config.get('auto_scan_enabled', True))
        self._auto_scan_cron = str(config.get('auto_scan_cron') or "*/10 * * * *").strip()
        self._integrity_retry_interval = self.__normalize_retry_minutes(config.get('integrity_retry_minutes')) * 60
        # 字幕生成设置
        self._translate_preference = config.get('translate_preference', 'english_first')
        self._enable_asr = config.get('enable_asr', True)
        self._asr_api_model = config.get('asr_api_model') or "whisper-1"
        self._asr_chunk_minutes = self.__normalize_asr_chunk_minutes(config.get('asr_chunk_minutes'))
        self._asr_chunk_seconds = self._asr_chunk_minutes * 60
        self._asr_request_timeout = self.__normalize_request_timeout(config.get('asr_request_timeout'), 300)
        asr_prompt = config.get('asr_prompt')
        if asr_prompt is None:
            asr_prompt = self.__default_asr_prompt()
        self._asr_prompt = str(asr_prompt or "").strip()
        self._translate_request_timeout = self.__normalize_request_timeout(config.get('translate_request_timeout'), 120)
        self._reuse_autosub_config = False
        self._auto_detect_language = config.get('auto_detect_language', False)
        self._translate_zh = config.get('translate_zh', False)
        self._enable_batch = config.get('enable_batch', True)
        self._batch_size = self.__normalize_batch_size(config.get('batch_size'))
        self._context_window = int(config.get('context_window')) if config.get('context_window') else 5
        self._max_retries = int(config.get('max_retries')) if config.get('max_retries') else 3
        self._enable_merge = config.get('enable_merge', False)
        self._openai = None

        if self._reset_tasks:
            config['reset_tasks'] = False
            config['clear_history'] = False
            self.update_config(config)
            self.reset_tasks()

        if not self._enabled and not self._run_now:
            self.stop_service()
            return

        api_required = self._translate_zh or self._enable_asr
        if api_required and not self.__resolve_openai_settings(config):
            logger.error("接口ASR或中文字幕翻译需要大模型接口配置，请在当前插件中维护接口地址和密钥")
            return

        if self._translate_zh:
            self._openai = OpenAi(api_key=self._openai_api_key, api_url=self._openai_api_url,
                                  proxy=settings.PROXY if self._openai_api_proxy else None,
                                  model=self._openai_model, compatible=bool(self._openai_api_compatible),
                                  timeout=self._translate_request_timeout, detailed_log=self._detailed_log)

        if self._enabled:
            alive_threads = [thread for thread in self._consumer_threads.values() if thread and thread.is_alive()]
            if not alive_threads:
                self._event.clear()
            logger.info("AI生成字幕服务已启动")
            # asr 配置检查
            if self._enable_asr and not self.__check_asr():
                return

            started_now = False
            if not self._running:
                self._task_queue = queue.Queue()
                self._running = True
                started_now = True
            self.__start_workers()
            if started_now or not alive_threads:
                self.__enqueue_existing_tasks()

            if self._retry_failed_once:
                config['retry_failed_once'] = False
                self.update_config(config)
                self.retry_failed_tasks_once()

            if self._run_now:
                config['run_now'] = False
                self.update_config(config)
                logger.info("立即运行一次")
                self._run_at_once(path_list=self._path_list)
            elif self._auto_scan_enabled and self._path_list:
                self.auto_scan_media_files(reason="插件启动扫描")
        else:
            self.stop_service()

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled or not self._auto_scan_enabled or not self._path_list:
            return []
        try:
            trigger = CronTrigger.from_crontab(self._auto_scan_cron or "*/10 * * * *")
        except Exception as err:
            logger.error(f"AI字幕自动生成定时扫描 cron 无效：{self._auto_scan_cron} - {err}")
            return []
        return [{
            "id": "AutoSubRemoteAsrAutoScan",
            "name": "接口ASR字幕定时扫描",
            "trigger": trigger,
            "func": self.auto_scan_media_files,
            "kwargs": {}
        }]

    def __start_workers(self):
        self.__ensure_runtime_state()
        if not self._task_queue:
            self._task_queue = queue.Queue()
        if self._event.is_set() and self._enabled:
            self._event.clear()
        for worker_index, thread in list(self._consumer_threads.items()):
            if not thread.is_alive():
                self._consumer_threads.pop(worker_index, None)
        for worker_index in range(1, self._parallel_tasks + 1):
            thread = self._consumer_threads.get(worker_index)
            if thread and thread.is_alive():
                continue
            thread = threading.Thread(
                target=self._consume_tasks,
                args=(worker_index,),
                name=f"autosubremoteasr-worker-{worker_index}",
                daemon=True
            )
            thread.start()
            self._consumer_threads[worker_index] = thread
        self._consumer_thread = self._consumer_threads.get(1)
        logger.info(f"任务队列和消费者线程已启动，并行任务数：{self._parallel_tasks}")

    def __enqueue_task(self, task: Optional[TaskItem]) -> bool:
        self.__ensure_runtime_state()
        if not self._running or not self._task_queue or not task:
            return False
        with self._tasks_lock:
            if task.task_id in self._queued_task_ids or task.task_id in self._current_processing_tasks:
                return False
            self._queued_task_ids.add(task.task_id)
        self._task_queue.put(task)
        return True

    def __enqueue_existing_tasks(self):
        now = datetime.now()
        changed = False
        with self._tasks_lock:
            tasks = list((self._tasks or {}).values())
        for task in tasks:
            if task.status == TaskStatus.IN_PROGRESS:
                task.status = TaskStatus.PENDING
                task.complete_time = None
                task.progress_stage = "等待重新处理"
                task.progress_detail = "服务重载后重新排队"
                task.progress_updated = now
                changed = True
            if task.status == TaskStatus.PENDING:
                self.__enqueue_task(task)
            elif task.status == TaskStatus.WAITING_FILE:
                if task.next_retry_time and task.next_retry_time > now:
                    self.__schedule_waiting_file_retry(task)
                else:
                    self.__enqueue_task(task)
        if changed:
            self.save_tasks()

    def __repair_queue_state(self, reason: str = "健康检查") -> int:
        self.__ensure_runtime_state()
        if not self._enabled:
            return 0
        if not self._running:
            self._task_queue = queue.Queue()
            self._running = True
        if self._tasks is None:
            self._tasks = self.load_tasks()
        with self._tasks_lock:
            has_pending_tasks = any(
                task.status == TaskStatus.PENDING
                for task in (self._tasks or {}).values()
            )
            has_current_tasks = bool(self._current_processing_tasks)
            has_queued_marks = bool(self._queued_task_ids)
        if self._event.is_set():
            self._event.clear()
            logger.warn(f"AI字幕队列自修复：{reason} 清理停止标记")
        alive_workers = [thread for thread in self._consumer_threads.values() if thread and thread.is_alive()]
        if has_pending_tasks and not has_current_tasks:
            if has_queued_marks:
                with self._tasks_lock:
                    self._queued_task_ids = set()
                logger.warn(f"AI字幕队列自修复：{reason} 清理旧队列标记")
            if alive_workers:
                self._consumer_threads = {}
                alive_workers = []
                logger.warn(f"AI字幕队列自修复：{reason} 重建消费者线程注册")
            self._translate_lock = threading.Semaphore(self._translate_concurrency)
            logger.warn(f"AI字幕队列自修复：{reason} 重置翻译并发锁")
        if len(alive_workers) < self._parallel_tasks:
            self.__start_workers()

        now = datetime.now()
        stale_seconds = max(90, int(self._translate_request_timeout or 60) + 30)
        repair_tasks = []
        changed = False
        with self._tasks_lock:
            current_ids = set(self._current_processing_tasks.keys())
            queued_ids = set(self._queued_task_ids)
            for task in list((self._tasks or {}).values()):
                if task.status == TaskStatus.IN_PROGRESS:
                    updated = task.progress_updated or task.add_time or now
                    stale = (now - updated).total_seconds() >= stale_seconds
                    if task.task_id not in current_ids or stale:
                        self._current_processing_tasks.pop(task.task_id, None)
                        if self._current_processing_task and self._current_processing_task.task_id == task.task_id:
                            self._current_processing_task = None
                        self._queued_task_ids.discard(task.task_id)
                        current_ids.discard(task.task_id)
                        queued_ids.discard(task.task_id)
                        task.status = TaskStatus.PENDING
                        task.complete_time = None
                        task.progress_stage = "等待重新处理"
                        task.progress_detail = f"{reason}发现任务未继续消费，已重新排队"
                        task.progress_updated = now
                        self._tasks[task.task_id] = task
                        repair_tasks.append(task)
                        changed = True
                elif task.status == TaskStatus.PENDING:
                    if task.task_id in current_ids:
                        self._current_processing_tasks.pop(task.task_id, None)
                        if self._current_processing_task and self._current_processing_task.task_id == task.task_id:
                            self._current_processing_task = None
                        current_ids.discard(task.task_id)
                    if task.task_id not in queued_ids:
                        repair_tasks.append(task)
            if changed:
                self.save_tasks()

        repaired = 0
        for task in repair_tasks:
            if self.__enqueue_task(task):
                repaired += 1
        if repaired:
            logger.warn(f"AI字幕队列自修复：{reason} 重新加入 {repaired} 个任务")
        return repaired

    def __schedule_waiting_file_retry(self, task: TaskItem):
        self.__ensure_runtime_state()
        if not task:
            return
        if not task.next_retry_time:
            task.next_retry_time = datetime.now() + timedelta(seconds=self._integrity_retry_interval)
        target_ts = task.next_retry_time.timestamp()
        with self._tasks_lock:
            existing_ts = self._scheduled_retry_tasks.get(task.task_id)
            if existing_ts and existing_ts >= target_ts - 1:
                return
            self._scheduled_retry_tasks[task.task_id] = target_ts
        delay = max(0.0, target_ts - time.time())
        thread = threading.Thread(
            target=self.__retry_waiting_file_later,
            args=(task.task_id, delay),
            name=f"autosubremoteasr-retry-{task.task_id[:8]}",
            daemon=True
        )
        thread.start()

    def __retry_waiting_file_later(self, task_id: str, delay: float):
        end_time = time.time() + delay
        while not self._event.is_set() and time.time() < end_time:
            time.sleep(min(5, max(0.2, end_time - time.time())))
        with self._tasks_lock:
            self._scheduled_retry_tasks.pop(task_id, None)
        if self._event.is_set():
            return
        self.__requeue_waiting_file_task(task_id, "到达文件完整性重试时间")

    def __requeue_waiting_file_task(self, task_id: str, detail: str) -> bool:
        self.__ensure_runtime_state()
        with self._tasks_lock:
            task = (self._tasks or {}).get(task_id)
            if not self._running or not self._task_queue or not task or task.status != TaskStatus.WAITING_FILE:
                return False
            task.status = TaskStatus.PENDING
            task.complete_time = None
            task.next_retry_time = None
            task.progress_stage = "等待重新检查"
            task.progress_detail = detail
            task.progress_updated = datetime.now()
            self._tasks[task_id] = task
            self.save_tasks()
        self.__enqueue_task(task)
        logger.info(f"等待完整的视频已重新加入队列：{task.video_file}")
        return True

    def __mark_waiting_file(self, task: Optional[TaskItem], error: str):
        if not task:
            return
        next_retry_time = datetime.now() + timedelta(seconds=self._integrity_retry_interval)
        task.status = TaskStatus.WAITING_FILE
        task.complete_time = None
        task.integrity_retry_count = int(task.integrity_retry_count or 0) + 1
        task.next_retry_time = next_retry_time
        detail = (error or "视频还未完整，等待后重试")[:500]
        detail = f"{detail}；下次检查：{next_retry_time.strftime('%Y-%m-%d %H:%M:%S')}"
        self.__update_task_progress(
            task,
            task.progress if task.progress else 5,
            "等待文件完整",
            detail,
            force=True
        )
        self.__schedule_waiting_file_retry(task)

    @staticmethod
    def __default_task_progress(status: TaskStatus) -> Tuple[float, str]:
        if status == TaskStatus.COMPLETED:
            return 100.0, "处理完成"
        if status == TaskStatus.IGNORED:
            return 100.0, "已忽略"
        if status == TaskStatus.FAILED:
            return 0.0, "处理失败"
        if status == TaskStatus.IN_PROGRESS:
            return 1.0, "处理中"
        if status == TaskStatus.WAITING_FILE:
            return 5.0, "等待文件完整"
        return 0.0, "等待中"

    @staticmethod
    def __parse_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    @staticmethod
    def __is_incomplete_video_error(error: str) -> bool:
        error = (error or "").lower()
        markers = [
            "moov atom not found",
            "ebml header parsing failed",
            "invalid data found when processing input",
            "error opening input",
            "end of file",
            "partial file",
            "truncated",
            "corrupt",
        ]
        return any(marker in error for marker in markers)

    def __is_waiting_file_task(self, task: TaskItem) -> bool:
        if not task:
            return False
        return (
            task.status == TaskStatus.WAITING_FILE
            or task.progress_stage in {"等待文件完整", "视频不完整"}
            or self.__is_incomplete_video_error(task.progress_detail)
        )

    @staticmethod
    def __is_existing_subtitle_task(task: TaskItem) -> bool:
        if not task or task.status != TaskStatus.IGNORED:
            return False
        text = f"{task.progress_stage or ''} {task.progress_detail or ''}"
        markers = ("已存在字幕", "已有字幕", "目标字幕已存在", "字幕文件已经存在", "外挂字幕", "内嵌字幕", "硬字幕")
        return any(marker in text for marker in markers)

    @staticmethod
    def __is_same_language_skip_task(task: TaskItem) -> bool:
        if not task or task.status != TaskStatus.COMPLETED:
            return False
        text = f"{task.progress_stage or ''} {task.progress_detail or ''}"
        markers = ("同语言跳过", "无需翻译", "原始语言已是中文", "源字幕语言已是中文",
                   "字幕已是中文", "匹配目标翻译语言")
        return any(marker in text for marker in markers)

    def load_tasks(self) -> Dict[str, TaskItem]:
        raw_tasks = self.get_data("tasks") or {}
        tasks = {}
        for task_id, task_dict in raw_tasks.items():
            try:
                status = TaskStatus(task_dict["status"])
                progress_stage = task_dict.get("progress_stage") or ""
                progress_detail = task_dict.get("progress_detail") or ""
                if self.__is_incomplete_video_error(progress_detail) or progress_stage in ["等待文件完整", "视频不完整"]:
                    status = TaskStatus.WAITING_FILE
                elif status == TaskStatus.FAILED and (
                        progress_stage == "服务已停止"
                        or "插件停止时任务未完成" in progress_detail
                ):
                    status = TaskStatus.PENDING
                default_progress, default_stage = self.__default_task_progress(status)
                task = TaskItem(
                    task_id=task_dict["task_id"],
                    video_file=task_dict["video_file"],
                    source=TaskSource(task_dict["source"]),
                    add_time=datetime.fromisoformat(task_dict["add_time"]),
                    status=status,
                    complete_time=None if status in [TaskStatus.PENDING, TaskStatus.WAITING_FILE]
                    else self.__parse_datetime(task_dict.get("complete_time")),
                    progress=float(task_dict.get("progress", default_progress) or 0.0),
                    progress_stage=progress_stage or default_stage,
                    progress_detail=progress_detail,
                    progress_updated=self.__parse_datetime(task_dict.get("progress_updated")),
                    integrity_retry_count=int(task_dict.get("integrity_retry_count") or 0),
                    next_retry_time=self.__parse_datetime(task_dict.get("next_retry_time")),
                )
                if status == TaskStatus.WAITING_FILE and not task.next_retry_time:
                    task.next_retry_time = datetime.now()
                    task.progress_stage = "等待文件完整"
                tasks[task_id] = task
            except Exception as e:
                logger.error(f"恢复任务失败：{e}")
        return tasks

    @staticmethod
    def _serialize_task(task: TaskItem) -> dict:
        return {
            "task_id": task.task_id,
            "video_file": task.video_file,
            "source": task.source.value,
            "add_time": task.add_time.isoformat() if task.add_time else None,
            "status": task.status.value,
            "complete_time": task.complete_time.isoformat() if task.complete_time else None,
            "progress": round(float(task.progress or 0), 1),
            "progress_stage": task.progress_stage or "",
            "progress_detail": task.progress_detail or "",
            "progress_updated": task.progress_updated.isoformat() if task.progress_updated else None,
            "integrity_retry_count": int(task.integrity_retry_count or 0),
            "next_retry_time": task.next_retry_time.isoformat() if task.next_retry_time else None,
        }

    def save_tasks(self):
        self.__ensure_runtime_state()
        with self._tasks_lock:
            tasks_dict = {task_id: self._serialize_task(task) for task_id, task in (self._tasks or {}).items()}
            self.save_data("tasks", tasks_dict)

    @staticmethod
    def __asr_checkpoint_key(task: Optional[TaskItem], video_file: str) -> Optional[str]:
        if task and task.task_id:
            return task.task_id
        return video_file or None

    def __load_asr_checkpoints_unlocked(self) -> Dict[str, dict]:
        checkpoints = self.get_data("asr_checkpoints") or {}
        return checkpoints if isinstance(checkpoints, dict) else {}

    def __get_asr_checkpoint(self, task: Optional[TaskItem], video_file: str, expected_chunks: int,
                             audio_lang: str) -> Optional[dict]:
        key = self.__asr_checkpoint_key(task, video_file)
        if not key:
            return None
        try:
            with self._tasks_lock:
                checkpoint = self.__load_asr_checkpoints_unlocked().get(key)
            if not isinstance(checkpoint, dict):
                return None
            if checkpoint.get("video_file") != video_file:
                return None
            signature = self.__video_file_signature(video_file)
            if checkpoint.get("source_size") is not None and int(checkpoint.get("source_size") or 0) != signature["size"]:
                return None
            if checkpoint.get("source_mtime") is not None and int(checkpoint.get("source_mtime") or 0) != signature["mtime"]:
                return None
            if int(checkpoint.get("expected_chunks") or 0) != int(expected_chunks or 0):
                return None
            if int(checkpoint.get("asr_chunk_seconds") or 0) != int(self._asr_chunk_seconds or 0):
                return None
            if checkpoint.get("asr_api_model") != self._asr_api_model:
                return None
            if checkpoint.get("audio_lang") != audio_lang:
                return None
            chunks = checkpoint.get("chunks") or {}
            if chunks:
                logger.info(f"恢复接口ASR断点：任务 {key} 已完成 {len(chunks)}/{expected_chunks or '?'} 段")
            return checkpoint
        except Exception as err:
            logger.warn(f"读取接口ASR断点失败：{err}")
            return None

    def __save_asr_chunk_checkpoint(self, task: Optional[TaskItem], video_file: str, expected_chunks: int,
                                    audio_lang: str, chunk_no: int, response: dict,
                                    detected_lang: Optional[str]):
        key = self.__asr_checkpoint_key(task, video_file)
        if not key:
            return
        try:
            now = datetime.now().isoformat()
            signature = self.__video_file_signature(video_file)
            with self._tasks_lock:
                checkpoints = self.__load_asr_checkpoints_unlocked()
                checkpoint = checkpoints.get(key) if isinstance(checkpoints.get(key), dict) else {}
                checkpoint.update({
                    "task_id": task.task_id if task else None,
                    "video_file": video_file,
                    "source_size": signature["size"],
                    "source_mtime": signature["mtime"],
                    "expected_chunks": int(expected_chunks or 0),
                    "asr_chunk_seconds": int(self._asr_chunk_seconds or 0),
                    "asr_api_model": self._asr_api_model,
                    "audio_lang": audio_lang,
                    "detected_lang": detected_lang,
                    "updated_at": now,
                })
                chunks = checkpoint.get("chunks") if isinstance(checkpoint.get("chunks"), dict) else {}
                chunks[str(chunk_no)] = copy.deepcopy(response or {})
                checkpoint["chunks"] = chunks
                checkpoints[key] = checkpoint
                if len(checkpoints) > 100:
                    items = sorted(checkpoints.items(), key=lambda item: (item[1] or {}).get("updated_at") or "")
                    for old_key, _ in items[:len(checkpoints) - 100]:
                        checkpoints.pop(old_key, None)
                self.save_data("asr_checkpoints", checkpoints)
        except Exception as err:
            logger.warn(f"保存接口ASR断点失败：{err}")

    def __clear_asr_checkpoint(self, task: Optional[TaskItem], video_file: str):
        key = self.__asr_checkpoint_key(task, video_file)
        if not key:
            return
        try:
            with self._tasks_lock:
                checkpoints = self.__load_asr_checkpoints_unlocked()
                if key in checkpoints:
                    checkpoints.pop(key, None)
                    self.save_data("asr_checkpoints", checkpoints)
        except Exception as err:
            logger.warn(f"清理接口ASR断点失败：{err}")

    @staticmethod
    def __video_file_signature(video_file: str) -> Dict[str, int]:
        try:
            stat = os.stat(video_file)
            return {"size": int(stat.st_size), "mtime": int(stat.st_mtime)}
        except Exception:
            return {"size": 0, "mtime": 0}

    def __language_probe_cache_key(self, video_file: str, audio_index: int) -> Tuple[str, Dict[str, int]]:
        signature = self.__video_file_signature(video_file)
        audio_index = int(audio_index or 0)
        raw = (
            f"{video_file}|size={signature['size']}|mtime={signature['mtime']}|"
            f"audio={audio_index}|model={self._asr_api_model}|probe=v3"
        )
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest(), signature

    def __load_language_probe_cache_unlocked(self) -> Dict[str, dict]:
        cache = self.get_data("asr_language_probe_cache") or {}
        return cache if isinstance(cache, dict) else {}

    def __get_cached_language_probe(self, video_file: str, audio_index: int) -> Optional[str]:
        try:
            key, signature = self.__language_probe_cache_key(video_file, audio_index)
            with self._tasks_lock:
                entry = self.__load_language_probe_cache_unlocked().get(key)
            if not isinstance(entry, dict):
                return None
            language = self.__normalize_language_code(entry.get("language"), fallback="")
            if not language:
                return None
            if entry.get("video_file") != video_file:
                return None
            if int(entry.get("audio_index") or 0) != int(audio_index or 0):
                return None
            if entry.get("asr_api_model") != self._asr_api_model:
                return None
            if int(entry.get("source_size") or 0) != signature["size"]:
                return None
            if int(entry.get("source_mtime") or 0) != signature["mtime"]:
                return None
            logger.info(
                f"复用全局语言探测缓存：language={language}，"
                f"samples={entry.get('sample_count') or '?'}，updated={entry.get('updated_at') or '-'}"
            )
            return language
        except Exception as err:
            logger.warn(f"读取全局语言探测缓存失败：{err}")
            return None

    def __save_cached_language_probe(self, video_file: str, audio_index: int, language: str,
                                     reason: str, sample_count: int, valid_count: int):
        language = self.__normalize_language_code(language, fallback="")
        if not language:
            return
        try:
            key, signature = self.__language_probe_cache_key(video_file, audio_index)
            now = datetime.now().isoformat()
            with self._tasks_lock:
                cache = self.__load_language_probe_cache_unlocked()
                cache[key] = {
                    "video_file": video_file,
                    "audio_index": int(audio_index or 0),
                    "source_size": signature["size"],
                    "source_mtime": signature["mtime"],
                    "asr_api_model": self._asr_api_model,
                    "language": language,
                    "reason": reason,
                    "sample_count": int(sample_count or 0),
                    "valid_count": int(valid_count or 0),
                    "updated_at": now,
                }
                if len(cache) > 200:
                    records = sorted(cache.items(), key=lambda item: (item[1] or {}).get("updated_at") or "")
                    for old_key, _ in records[:len(cache) - 200]:
                        cache.pop(old_key, None)
                self.save_data("asr_language_probe_cache", cache)
        except Exception as err:
            logger.warn(f"保存全局语言探测缓存失败：{err}")

    @staticmethod
    def __checkpoint_path(path) -> str:
        return str(path) if path is not None else ""

    @staticmethod
    def __translate_checkpoint_key(task: Optional[TaskItem], source_subtitle: str, dest_subtitle: str) -> Optional[str]:
        if task and task.task_id:
            return task.task_id
        source_path = AutoSubRemoteAsr.__checkpoint_path(source_subtitle)
        dest_path = AutoSubRemoteAsr.__checkpoint_path(dest_subtitle)
        if source_path and dest_path:
            return f"{source_path}->{dest_path}"
        return None

    @staticmethod
    def __file_mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except Exception:
            return 0.0

    def __load_translate_checkpoints_unlocked(self) -> Dict[str, dict]:
        checkpoints = self.get_data("translate_checkpoints") or {}
        return checkpoints if isinstance(checkpoints, dict) else {}

    def __get_translate_checkpoint(self, task: Optional[TaskItem], source_subtitle: str, dest_subtitle: str,
                                   total: int) -> Optional[dict]:
        key = self.__translate_checkpoint_key(task, source_subtitle, dest_subtitle)
        if not key:
            return None
        source_path = self.__checkpoint_path(source_subtitle)
        dest_path = self.__checkpoint_path(dest_subtitle)
        try:
            with self._tasks_lock:
                checkpoint = self.__load_translate_checkpoints_unlocked().get(key)
            if not isinstance(checkpoint, dict):
                return None
            if checkpoint.get("source_subtitle") != source_path:
                return None
            if checkpoint.get("dest_subtitle") != dest_path:
                return None
            if int(checkpoint.get("total") or 0) != int(total or 0):
                return None
            if float(checkpoint.get("source_mtime") or 0) != float(self.__file_mtime(source_path)):
                return None
            items = checkpoint.get("items") or {}
            if items:
                logger.info(f"恢复字幕翻译断点：任务 {key} 已完成 {len(items)}/{total or '?'} 行")
            return checkpoint
        except Exception as err:
            logger.warn(f"读取字幕翻译断点失败：{err}")
            return None

    def __save_translate_checkpoint(self, task: Optional[TaskItem], source_subtitle: str, dest_subtitle: str,
                                    total: int, processed: List[srt.Subtitle]):
        key = self.__translate_checkpoint_key(task, source_subtitle, dest_subtitle)
        if not key:
            return
        try:
            source_path = self.__checkpoint_path(source_subtitle)
            dest_path = self.__checkpoint_path(dest_subtitle)
            now = datetime.now().isoformat()
            items = {
                str(index): item.content
                for index, item in enumerate(processed or [])
            }
            with self._tasks_lock:
                checkpoints = self.__load_translate_checkpoints_unlocked()
                checkpoints[key] = {
                    "task_id": task.task_id if task else None,
                    "source_subtitle": source_path,
                    "dest_subtitle": dest_path,
                    "source_mtime": self.__file_mtime(source_path),
                    "total": int(total or 0),
                    "items": items,
                    "updated_at": now,
                }
                if len(checkpoints) > 100:
                    records = sorted(checkpoints.items(), key=lambda item: (item[1] or {}).get("updated_at") or "")
                    for old_key, _ in records[:len(checkpoints) - 100]:
                        checkpoints.pop(old_key, None)
                self.save_data("translate_checkpoints", checkpoints)
        except Exception as err:
            logger.warn(f"保存字幕翻译断点失败：{err}")

    def __clear_translate_checkpoint(self, task: Optional[TaskItem], source_subtitle: str, dest_subtitle: str):
        key = self.__translate_checkpoint_key(task, source_subtitle, dest_subtitle)
        if not key:
            return
        try:
            with self._tasks_lock:
                checkpoints = self.__load_translate_checkpoints_unlocked()
                if key in checkpoints:
                    checkpoints.pop(key, None)
                    self.save_data("translate_checkpoints", checkpoints)
        except Exception as err:
            logger.warn(f"清理字幕翻译断点失败：{err}")

    def __clear_task_checkpoints(self, task: Optional[TaskItem]):
        if not task or not task.task_id:
            return
        try:
            with self._tasks_lock:
                for data_key in ("asr_checkpoints", "translate_checkpoints"):
                    checkpoints = self.get_data(data_key) or {}
                    if isinstance(checkpoints, dict) and task.task_id in checkpoints:
                        checkpoints.pop(task.task_id, None)
                        self.save_data(data_key, checkpoints)
        except Exception as err:
            logger.warn(f"清理任务断点失败：{err}")

    @staticmethod
    def __clip_progress(progress: float) -> float:
        try:
            return round(max(0.0, min(100.0, float(progress))), 1)
        except Exception:
            return 0.0

    @staticmethod
    def __scale_progress(start: float, end: float, current: float, total: float) -> float:
        try:
            if total <= 0:
                return start
            ratio = max(0.0, min(1.0, float(current) / float(total)))
            return start + (end - start) * ratio
        except Exception:
            return start

    def __update_task_progress(self, task: Optional[TaskItem] = None, progress: Optional[float] = None,
                               stage: Optional[str] = None, detail: Optional[str] = None,
                               force: bool = False):
        self.__ensure_runtime_state()
        if not task and len(self._current_processing_tasks) == 1:
            task = next(iter(self._current_processing_tasks.values()))
        if not task:
            return
        with self._tasks_lock:
            if progress is not None:
                task.progress = self.__clip_progress(progress)
            if stage is not None:
                task.progress_stage = stage
            if detail is not None:
                task.progress_detail = detail
            task.progress_updated = datetime.now()
            self._tasks[task.task_id] = task

            now = time.time()
            last_save_at = self._progress_save_at.get(task.task_id, 0)
            if force or now - last_save_at >= 3 or task.progress >= 100:
                self.save_tasks()
                self._progress_save_at[task.task_id] = now

    def add_task(self, video_file: str, source: TaskSource):
        """
        添加新任务到队列和任务列表中，若任务已存在则跳过。
        :param video_file: 视频文件路径
        :param source: 任务来源（手动/事件）
        """
        task = TaskItem(
            task_id=str(uuid4()),
            video_file=video_file,
            source=source,
            add_time=datetime.now()
        )

        if self.__is_duplicate_task(task.video_file):
            logger.info(f"任务已存在，跳过添加：{video_file}")
            return False

        if not self._running or not self._task_queue:
            logger.warn(f"任务队列未启动，跳过添加：{video_file}")
            return False

        if not self.__enqueue_task(task):
            logger.info(f"任务已在队列或处理中，跳过重复添加：{video_file}")
            return False
        with self._tasks_lock:
            self._tasks[task.task_id] = task
            self.save_tasks()
        logger.info(f"加入任务队列: {video_file}")
        return True

    def __drain_task_queue(self, keep_task_ids: Optional[set] = None) -> int:
        if not self._task_queue:
            return 0
        kept_tasks = []
        removed = 0
        while True:
            try:
                queued_task = self._task_queue.get_nowait()
            except queue.Empty:
                break
            if queued_task is not None and (keep_task_ids is None or queued_task.task_id in keep_task_ids):
                kept_tasks.append(queued_task)
            else:
                removed += 1
            self.__safe_task_done()
        for queued_task in kept_tasks:
            self._task_queue.put(queued_task)
        return removed

    def reset_tasks(self) -> int:
        self.__ensure_runtime_state()
        with self._tasks_lock:
            tasks = self._tasks or {}
            keep_task_ids = {
                task_id
                for task_id, task in tasks.items()
                if task.status == TaskStatus.IN_PROGRESS
            }
            removed_task_ids = set(tasks.keys()) - keep_task_ids
            self._tasks = {
                task_id: task
                for task_id, task in tasks.items()
                if task_id in keep_task_ids
            }
            self._queued_task_ids = {
                task_id
                for task_id in (self._queued_task_ids or set())
                if task_id in keep_task_ids
            }
            for task_id in removed_task_ids:
                self._scheduled_retry_tasks.pop(task_id, None)
            self.save_tasks()
            self.save_data("asr_checkpoints", {})
            self.save_data("translate_checkpoints", {})
        removed_from_queue = self.__drain_task_queue(keep_task_ids)
        removed = len(removed_task_ids)
        logger.info(
            f"插件任务已重置：移除记录 {removed} 条，清空待处理队列 {removed_from_queue} 条，"
            "保留正在处理任务"
        )
        return removed

    def clear_tasks(self) -> int:
        return self.reset_tasks()

    def retry_failed_tasks_once(self) -> int:
        self.__ensure_runtime_state()
        if not self._running or not self._task_queue:
            logger.warn("任务队列未启动，无法重试失败任务")
            return 0

        retry_tasks = []
        now = datetime.now()
        with self._tasks_lock:
            for task in (self._tasks or {}).values():
                if task.status != TaskStatus.FAILED:
                    continue
                task.status = TaskStatus.PENDING
                task.complete_time = None
                task.progress = 0.0
                task.progress_stage = "等待重试"
                task.progress_detail = "手动触发失败任务重试"
                task.progress_updated = now
                self._tasks[task.task_id] = task
                retry_tasks.append(task)
            if retry_tasks:
                self.save_tasks()

        for task in retry_tasks:
            self.__enqueue_task(task)
        logger.info(f"失败任务已重新加入队列：{len(retry_tasks)}")
        return len(retry_tasks)

    def __find_latest_task_by_video_file(self, video_file: str) -> Optional[TaskItem]:
        self.__ensure_runtime_state()
        with self._tasks_lock:
            matched = [
                task for task in (self._tasks or {}).values()
                if task.video_file == video_file
            ]
        if not matched:
            return None
        return max(matched, key=lambda item: item.add_time or datetime.min)

    def __record_existing_subtitle_task(self, video_file: str, source: TaskSource, reason: str) -> bool:
        self.__ensure_runtime_state()
        now = datetime.now()
        task = TaskItem(
            task_id=str(uuid4()),
            video_file=video_file,
            source=source,
            add_time=now,
            status=TaskStatus.IGNORED,
            complete_time=now,
            progress=100.0,
            progress_stage="已存在字幕",
            progress_detail=reason or "字幕已存在",
            progress_updated=now,
        )
        with self._tasks_lock:
            if self.__find_latest_task_by_video_file(video_file):
                return False
            self._tasks[task.task_id] = task
            self.save_tasks()
        logger.info(f"字幕已存在，记录到已存在字幕分类：{video_file} - {reason}")
        return True

    def __is_duplicate_task(self, video_file: str) -> bool:
        self.__ensure_runtime_state()
        with self._tasks_lock:
            for task in (self._tasks or {}).values():
                if task.video_file == video_file and task.status in [
                    TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.WAITING_FILE
                ]:
                    return True
        return False

    def __safe_task_done(self):
        if not self._task_queue:
            return
        try:
            self._task_queue.task_done()
        except ValueError:
            logger.debug("任务队列完成计数已归零，忽略重复 task_done")

    def _consume_tasks(self, worker_index: int = 1):
        while not self._event.is_set():
            if worker_index > self._parallel_tasks:
                break
            task = None
            try:
                task = self._task_queue.get(timeout=1)
                if task is None:
                    self.__safe_task_done()
                    continue
                with self._tasks_lock:
                    self._queued_task_ids.discard(task.task_id)
                    latest_task = (self._tasks or {}).get(task.task_id)
                    if not latest_task or latest_task.status not in [TaskStatus.PENDING, TaskStatus.WAITING_FILE]:
                        logger.info(f"跳过已清理或非待处理任务：{task.video_file}")
                        self.__safe_task_done()
                        continue
                    task = latest_task
                    self._current_processing_task = task if worker_index == 1 else self._current_processing_task
                    self._current_processing_tasks[task.task_id] = task
                logger.info(f"工作线程 {worker_index} 开始处理任务 {task.task_id}: {task.video_file}")
                task.status = TaskStatus.IN_PROGRESS
                task.progress = 1.0
                task.progress_stage = "准备处理"
                task.progress_detail = "任务已开始"
                task.progress_updated = datetime.now()
                with self._tasks_lock:
                    self._tasks[task.task_id] = task
                    self.save_tasks()
                task.status = self.__process_autosub(task.video_file, task)
                if self._event.is_set() and task.status == TaskStatus.PENDING:
                    logger.info(f"工作线程 {worker_index} 收到停止信号，跳过旧任务状态回写：{task.video_file}")
                    self.__safe_task_done()
                    continue
                task.complete_time = datetime.now() if task.status in [
                    TaskStatus.COMPLETED, TaskStatus.IGNORED, TaskStatus.FAILED
                ] else None
                if task.status == TaskStatus.COMPLETED:
                    self.__clear_task_checkpoints(task)
                    if self.__is_same_language_skip_task(task):
                        self.__update_task_progress(
                            task,
                            100,
                            "同语言跳过",
                            task.progress_detail or "字幕语言已是中文，无需生成机翻字幕",
                            force=True,
                        )
                    else:
                        self.__update_task_progress(task, 100, "处理完成", "字幕处理完成", force=True)
                elif task.status == TaskStatus.IGNORED:
                    self.__clear_task_checkpoints(task)
                    ignored_stage = "已存在字幕" if self.__is_existing_subtitle_task(task) else "已忽略"
                    self.__update_task_progress(task, 100, ignored_stage, task.progress_detail or "任务已忽略", force=True)
                elif task.status == TaskStatus.FAILED:
                    self.__update_task_progress(task, task.progress, "处理失败",
                                                task.progress_detail or "任务处理失败", force=True)
                elif task.status == TaskStatus.WAITING_FILE:
                    self.__schedule_waiting_file_retry(task)
                with self._tasks_lock:
                    self._tasks[task.task_id] = task
                    self.save_tasks()
                self.__safe_task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"消费任务时发生异常: {e}")
                logger.error(traceback.format_exc())
                if task:
                    task.status = TaskStatus.FAILED
                    task.complete_time = datetime.now()
                    self.__update_task_progress(task, task.progress, "处理异常", str(e)[:120], force=True)
                    self.__safe_task_done()
            finally:
                if task:
                    with self._tasks_lock:
                        self._current_processing_tasks.pop(task.task_id, None)
                        if self._current_processing_task and self._current_processing_task.task_id == task.task_id:
                            self._current_processing_task = None
                        self._progress_save_at.pop(task.task_id, None)
        if self._event.is_set():
            logger.debug(f"消费线程 {worker_index} 已退出")
        else:
            logger.info(f"消费线程 {worker_index} 已退出")

    # 监听媒体入库事件，每个事件触发一次自动字幕任务
    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: MPEvent):
        """监听媒体入库事件"""
        if not self._listen_transfer_event:
            return
        item = event.event_data
        item_media: MediaInfo = item.get("mediainfo")
        logger.info(f"监听到媒体入库事件：{item_media.title}")
        origin_lang = item_media.original_language
        prefer_langs = ['zh', 'chi', 'zh-CN', 'chs', 'zhs', 'zh-Hans', 'zhong', 'simp', 'cn']
        if origin_lang in prefer_langs:
            logger.info(f"媒体原始语言为中文，跳过处理")
            return

        item_transfer: TransferInfo = item.get("transferinfo")
        item_file_list = item_transfer.file_list_new

        for file_path in item_file_list:
            if os.path.splitext(file_path)[-1].lower() in settings.RMT_MEDIAEXT:
                result = self.__scan_video_file_for_task(file_path, TaskSource.EVENT)
                if result == "excluded":
                    logger.info(f"媒体入库文件命中排除路径，跳过字幕任务：{file_path}")

    def auto_scan_media_files(self, reason: str = "定时扫描"):
        self.__ensure_runtime_state()
        if not self._auto_scan_enabled or not self._path_list:
            logger.info("AI字幕定时扫描跳过：未启用或未配置媒体路径")
            return
        if not self._running or not self._task_queue:
            logger.info("AI字幕定时扫描跳过：任务队列未启动")
            return
        with self._auto_scan_lock:
            if self._auto_scan_thread and self._auto_scan_thread.is_alive():
                logger.info("AI字幕定时扫描仍在运行，跳过本次触发")
                return
            thread = threading.Thread(
                target=self.__auto_scan_media_files,
                args=(reason,),
                name="autosubremoteasr-auto-scan",
                daemon=True
            )
            self._auto_scan_thread = thread
            thread.start()

    def __auto_scan_media_files(self, reason: str):
        start_time = time.time()
        stats = {
            "added": 0,
            "requeued": 0,
            "waiting": 0,
            "active": 0,
            "done": 0,
            "subtitle_exists": 0,
            "failed": 0,
            "invalid": 0,
            "excluded": 0,
            "skipped": 0,
        }
        logger.info(f"AI字幕{reason}开始，路径数：{len(self._path_list)}")
        try:
            for path in self._path_list:
                if self._event.is_set():
                    break
                if not os.path.exists(path) or not os.path.isabs(path):
                    stats["invalid"] += 1
                    logger.warn(f"AI字幕扫描路径无效，跳过：{path}")
                    continue
                if self.__is_excluded_path(path):
                    stats["excluded"] += 1
                    logger.info(f"AI字幕扫描路径命中排除规则，跳过：{path}")
                    continue
                try:
                    video_files = self.__get_library_files(path, self._exclude_path_list)
                    for video_file in video_files:
                        if self._event.is_set():
                            break
                        try:
                            result = self.__scan_video_file_for_task(video_file)
                            stats[result] = stats.get(result, 0) + 1
                        except Exception as err:
                            stats["skipped"] += 1
                            logger.error(f"AI字幕扫描文件异常，已跳过：{video_file} - {err}")
                            logger.error(traceback.format_exc())
                except Exception as err:
                    stats["skipped"] += 1
                    logger.error(f"AI字幕扫描路径异常，已跳过：{path} - {err}")
                    logger.error(traceback.format_exc())
        except Exception as e:
            logger.error(f"AI字幕{reason}异常：{e}")
            logger.error(traceback.format_exc())
        finally:
            seconds = round(time.time() - start_time, 2)
            logger.info(
                f"AI字幕{reason}完成：新增 {stats['added']}，重新排队 {stats['requeued']}，"
                f"等待完整 {stats['waiting']}，已在队列 {stats['active']}，已处理 {stats['done']}，"
                f"已有字幕 {stats['subtitle_exists']}，失败跳过 {stats['failed']}，"
                f"无效路径 {stats['invalid']}，排除 {stats['excluded']}，其他跳过 {stats['skipped']}，耗时 {seconds} 秒"
            )

    def __scan_video_file_for_task(self, video_file: str, source: TaskSource = TaskSource.AUTO_SCAN) -> str:
        if self.__is_excluded_path(video_file):
            logger.info(f"AI字幕扫描文件命中排除规则，跳过：{video_file}")
            return "excluded"
        now = datetime.now()
        latest_task = self.__find_latest_task_by_video_file(video_file)
        if latest_task:
            if latest_task.status == TaskStatus.PENDING:
                if self.__enqueue_task(latest_task):
                    latest_task.complete_time = None
                    latest_task.progress_stage = "等待重新处理"
                    latest_task.progress_detail = "扫描发现等待任务未在队列，已重新排队"
                    latest_task.progress_updated = now
                    with self._tasks_lock:
                        self._tasks[latest_task.task_id] = latest_task
                        self.save_tasks()
                    return "requeued"
                return "active"
            if latest_task.status == TaskStatus.IN_PROGRESS:
                return "active"
            if latest_task.status == TaskStatus.WAITING_FILE:
                if latest_task.next_retry_time and latest_task.next_retry_time > now:
                    self.__schedule_waiting_file_retry(latest_task)
                    return "waiting"
                if self.__requeue_waiting_file_task(latest_task.task_id, "定时扫描发现已到重试时间"):
                    return "requeued"
                return "waiting"
            if latest_task.status in [TaskStatus.COMPLETED, TaskStatus.IGNORED]:
                return "done"
            if latest_task.status == TaskStatus.FAILED:
                return "failed"

        subtitle_reason = self.__target_subtitle_reason(video_file)
        if subtitle_reason:
            self.__record_existing_subtitle_task(video_file, source, subtitle_reason)
            return "subtitle_exists"

        return "added" if self.add_task(video_file, source) else "skipped"

    def _run_at_once(self, path_list: List[str]):
        stats = {
            "added": 0,
            "requeued": 0,
            "waiting": 0,
            "active": 0,
            "done": 0,
            "subtitle_exists": 0,
            "failed": 0,
            "invalid": 0,
            "excluded": 0,
            "skipped": 0,
        }
        for path in path_list:
            if not os.path.exists(path) or not os.path.isabs(path):
                stats["invalid"] += 1
                logger.warn(f"目录/文件无效，不进行处理:{path}")
                continue
            if self.__is_excluded_path(path):
                stats["excluded"] += 1
                logger.info(f"手动扫描路径命中排除规则，跳过：{path}")
                continue
            if os.path.isdir(path):
                for video_file in self.__get_library_files(path, self._exclude_path_list):
                    result = self.__scan_video_file_for_task(video_file, TaskSource.MANUAL)
                    stats[result] = stats.get(result, 0) + 1
            elif os.path.splitext(path)[-1].lower() in settings.RMT_MEDIAEXT:
                result = self.__scan_video_file_for_task(path, TaskSource.MANUAL)
                stats[result] = stats.get(result, 0) + 1
        logger.info(
            f"AI字幕手动扫描完成：新增 {stats['added']}，重新排队 {stats['requeued']}，"
            f"等待完整 {stats['waiting']}，已在队列 {stats['active']}，已处理 {stats['done']}，"
            f"已有字幕 {stats['subtitle_exists']}，失败跳过 {stats['failed']}，"
            f"无效路径 {stats['invalid']}，排除 {stats['excluded']}，其他跳过 {stats['skipped']}"
        )

    def __check_asr(self):
        if not self._openai_api_key:
            logger.warn("接口ASR缺少API密钥，不进行处理")
            return False
        if not self._asr_api_model:
            logger.warn("接口ASR模型未配置，不进行处理")
            return False
        return True

    @staticmethod
    def __get_video_duration(video_meta: Optional[dict]) -> float:
        try:
            duration = (video_meta or {}).get("format", {}).get("duration")
            return float(duration or 0)
        except Exception:
            return 0.0

    def __check_video_integrity(self, video_file: str, task: Optional[TaskItem] = None) -> bool:
        if self._event.is_set():
            raise UserInterruptException("用户中断当前任务")

        self.__update_task_progress(task, 5, "校验视频完整性", "正在读取视频元数据", force=True)
        video_meta = Ffmpeg().get_video_metadata(video_file, stop_event=self._event)
        if self._event.is_set():
            raise UserInterruptException("用户中断当前任务")
        if not video_meta:
            self.__mark_waiting_file(task, "读取视频元数据失败，可能文件仍未完整")
            return False

        duration = self.__get_video_duration(video_meta)
        streams = video_meta.get("streams") or []
        video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
        if not video_streams:
            self.__mark_waiting_file(task, "未读取到视频流，可能文件仍未完整")
            return False
        if duration <= 0:
            self.__mark_waiting_file(task, "未读取到有效时长，可能文件仍未完整")
            return False

        if not self._full_integrity_check:
            self.__update_task_progress(task, 24, "视频完整性通过", "元数据校验通过", force=True)
            return True

        def update_progress(out_time: float, total_duration: float):
            percent = self.__scale_progress(5, 24, out_time, total_duration)
            detail = f"{out_time:.1f}/{total_duration:.1f} 秒" if total_duration else "正在完整扫描视频"
            self.__update_task_progress(task, percent, "校验视频完整性", detail)

        self.__update_task_progress(task, 5, "校验视频完整性", "正在完整解码扫描视频", force=True)
        ok, error = Ffmpeg().check_video_integrity(
            video_file,
            duration=duration,
            progress_callback=update_progress if duration else None,
            stop_event=self._event,
            threads=self._cpu_threads
        )
        if self._event.is_set():
            raise UserInterruptException("用户中断当前任务")
        if ok:
            self.__update_task_progress(task, 24, "视频完整性通过", "视频可完整解码", force=True)
            return True

        logger.warn(f"视频完整性校验失败：{video_file} - {error}")
        self.__mark_waiting_file(task, error or "视频完整性校验失败")
        return False

    def __audio_probe_prefer_langs(self) -> Optional[List[str]]:
        if self._translate_preference in {"english_only", "english_first"}:
            return ["en", "eng"]
        return None

    def __detect_target_language_before_hard_subtitle(self, video_file: str,
                                                      task: Optional[TaskItem] = None) -> str:
        if not self.__check_asr() or not self._translate_zh:
            return ""

        try:
            self.__update_task_progress(task, 24, "目标语言预检", "正在读取音轨语言", force=True)
            video_meta = Ffmpeg().get_video_metadata(video_file, stop_event=self._event)
            if self._event.is_set():
                raise UserInterruptException("用户中断当前任务")
            if not video_meta:
                logger.warn(f"目标语言预检跳过：读取视频元数据失败 {video_file}")
                return ""

            ret, audio_index, audio_lang = self.__get_video_prefer_audio(
                video_meta,
                prefer_lang=self.__audio_probe_prefer_langs(),
            )
            if not ret:
                logger.warn(f"目标语言预检跳过：未读取到可用音轨 {video_file}")
                return ""

            if self._auto_detect_language or not iso639.find(audio_lang) or not iso639.to_iso639_1(audio_lang):
                duration = self.__get_video_duration(video_meta)
                with tempfile.TemporaryDirectory(prefix='autosubremoteasr-probe-') as audio_dir:
                    lang = self.__detect_global_audio_language(
                        video_file,
                        audio_index,
                        duration,
                        audio_dir,
                        task,
                    )
            else:
                lang = self.__normalize_language_code(audio_lang, fallback="")
                self.__update_task_progress(task, 24, "目标语言预检",
                                            f"音轨元数据语言 {lang}", force=True)

            if self.__is_target_language(lang):
                logger.info(f"目标语言预检命中：{video_file} language={lang}")
                return lang
            logger.info(f"目标语言预检未命中：{video_file} language={lang or '-'}")
            return ""
        except UserInterruptException:
            raise
        except Exception as err:
            logger.warn(f"目标语言预检失败，继续后续硬字幕检测：{video_file} - {err}")
            return ""

    def __process_autosub(self, video_file, task: Optional[TaskItem] = None) -> TaskStatus:
        if not video_file:
            return TaskStatus.FAILED

        start_time = time.time()
        file_path, file_ext = os.path.splitext(video_file)
        file_name = os.path.basename(video_file)

        try:
            logger.info(f"开始处理文件：{video_file} ...")
            self.__update_task_progress(task, 3, "准备处理", "正在检查字幕和媒体信息", force=True)
            # 判断目的字幕（和内嵌）是否已存在
            subtitle_reason = self.__target_subtitle_reason(video_file)
            if subtitle_reason:
                logger.warn(f"字幕已经存在，不进行处理：{subtitle_reason}")
                self.__update_task_progress(task, 100, "已存在字幕", subtitle_reason, force=True)
                return TaskStatus.IGNORED
            if not self.__check_video_integrity(video_file, task):
                return TaskStatus.WAITING_FILE if task and task.status == TaskStatus.WAITING_FILE else TaskStatus.FAILED
            target_lang = self.__detect_target_language_before_hard_subtitle(video_file, task)
            if target_lang:
                self.__update_task_progress(
                    task,
                    98,
                    "同语言跳过",
                    f"音轨语言已匹配目标翻译语言：{target_lang}，无需生成字幕",
                    force=True,
                )
                return TaskStatus.COMPLETED
            hard_subtitle_reason = self.__hard_subtitle_reason(video_file, task=task)
            if hard_subtitle_reason:
                logger.warn(f"检测到硬字幕，不进行处理：{hard_subtitle_reason}")
                self.__update_task_progress(task, 100, "已存在字幕", hard_subtitle_reason, force=True)
                return TaskStatus.IGNORED
            # 生成字幕
            ret, lang, gen_sub_path = self.__generate_subtitle(video_file, file_path, self._enable_asr, task)
            if not ret:
                if task and task.status == TaskStatus.WAITING_FILE:
                    return TaskStatus.WAITING_FILE
                message = f" 媒体: {file_name}\n 生成字幕失败，跳过后续处理"
                if self._send_notify:
                    self.post_message(mtype=NotificationType.Plugin, title="【自动字幕生成】", text=message)
                self.__update_task_progress(task, task.progress if task else 0, "处理失败", "生成字幕失败", force=True)
                return TaskStatus.FAILED

            if self.__is_target_language(lang):
                logger.info(f"原始语言已匹配目标翻译语言（{lang}），任务完成，不生成字幕文件")
                self.__update_task_progress(task, 98, "同语言跳过", f"原始语言已匹配目标翻译语言：{lang}", force=True)
            else:
                self.__update_task_progress(task, 75 if self._translate_zh else 95, "字幕已生成",
                                            f"原始语言：{lang}", force=True)
                if self._translate_zh:
                    # 翻译字幕
                    logger.info(f"开始翻译字幕为中文 ...")
                    if not self.__translate_zh_subtitle(lang, gen_sub_path, f"{file_path}.zh.机翻.srt", task):
                        message = f" 媒体: {file_name}\n 翻译字幕失败，跳过完成标记"
                        if self._send_notify:
                            self.post_message(mtype=NotificationType.Plugin, title="【自动字幕生成】", text=message)
                        self.__update_task_progress(task, task.progress if task else 0, "处理失败",
                                                    "中文字幕翻译失败", force=True)
                        return TaskStatus.FAILED
                    logger.info(f"翻译字幕完成：{file_name}.zh.机翻.srt")
                    self.__update_task_progress(task, 98, "翻译完成", "中文字幕已生成", force=True)

            end_time = time.time()
            message = f" 媒体: {file_name}\n 处理完成\n 字幕原始语言: {lang}\n "
            if self._translate_zh:
                if self.__is_target_language(lang):
                    message += "字幕已是中文，跳过翻译\n "
                else:
                    message += "字幕翻译语言: zh\n "
            message += f"耗时：{round(end_time - start_time, 2)}秒"
            logger.info(f"自动字幕生成 处理完成：{message}")
            if self._send_notify:
                self.post_message(mtype=NotificationType.Plugin, title="【自动字幕生成】", text=message)
            return TaskStatus.COMPLETED
        except UserInterruptException:
            logger.info(f"插件停止或重载，中断当前任务，后续将从断点恢复：{video_file}")
            if task:
                task.status = TaskStatus.PENDING
                task.complete_time = None
                task.progress_stage = "等待重新处理"
                task.progress_detail = "插件停止或重载，稍后从断点恢复"
                task.progress_updated = datetime.now()
            return TaskStatus.PENDING
        except Exception as e:
            logger.error(f"自动字幕生成 处理异常：{e}")
            end_time = time.time()
            message = f" 媒体: {file_name}\n 处理失败\n 耗时：{round(end_time - start_time, 2)}秒"
            if self._send_notify:
                self.post_message(mtype=NotificationType.Plugin, title="【自动字幕生成】", text=message)
            # 打印调用栈
            logger.error(traceback.format_exc())
            self.__update_task_progress(task, task.progress if task else 0, "处理异常", str(e)[:120], force=True)
            return TaskStatus.FAILED

    @staticmethod
    def __normalize_language_code(value: Optional[str], fallback: str = "und") -> str:
        if not value or value == "auto":
            return fallback
        try:
            language = iso639.to_iso639_1(value)
            return language or fallback
        except Exception:
            return fallback

    @classmethod
    def __is_chinese_language(cls, value: Optional[str]) -> bool:
        if not value:
            return False
        normalized = str(value).strip().lower().replace("_", "-")
        if not normalized or normalized == "auto":
            return False
        chinese_codes = {
            "zh", "chi", "zho", "chinese", "cn", "chs", "cht", "zhs", "zht",
            "zh-cn", "zh-sg", "zh-hans", "zh-hant", "zh-tw", "zh-hk", "zh-mo",
            "cmn", "mandarin", "yue", "cantonese", "zhong", "simp",
        }
        if normalized in chinese_codes:
            return True
        base = normalized.split("-", 1)[0]
        if base in chinese_codes:
            return True
        return cls.__normalize_language_code(normalized, fallback="") == "zh"

    def __is_target_language(self, value: Optional[str]) -> bool:
        if self._translate_zh:
            return self.__is_chinese_language(value)
        return False

    def __transcribe_audio_chunk(self, audio_file: str, audio_lang: str, use_prompt: bool = True) -> dict:
        request_lang = self.__normalize_language_code(audio_lang, fallback="")
        data = {
            "model": self._asr_api_model,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if request_lang:
            data["language"] = request_lang
        if use_prompt and self._asr_prompt:
            data["prompt"] = self._asr_prompt

        url = f"{self.__get_openai_base_url()}/audio/transcriptions"
        try:
            audio_size = os.path.getsize(audio_file)
        except Exception:
            audio_size = -1
        if self._detailed_log:
            logger.info(
                f"接口ASR请求：url={url} model={self._asr_api_model} timeout={self._asr_request_timeout}s "
                f"file={os.path.basename(audio_file)} size={audio_size} data={_log_preview(data)}"
            )
        else:
            logger.info(
                f"接口ASR请求：url={url} model={self._asr_api_model} timeout={self._asr_request_timeout}s "
                f"file={os.path.basename(audio_file)} size={audio_size}"
            )
        started_at = time.time()
        with self.__create_openai_http_client(timeout=self._asr_request_timeout) as client:
            with open(audio_file, "rb") as file_obj:
                try:
                    response = client.post(
                        url,
                        headers={"Authorization": f"Bearer {self._openai_api_key}"},
                        data=data,
                        files={"file": (os.path.basename(audio_file), file_obj, "audio/mpeg")},
                    )
                except Exception as err:
                    logger.error(
                        f"接口ASR请求异常：url={url} model={self._asr_api_model} "
                        f"elapsed={time.time() - started_at:.2f}s error={err}"
                    )
                    raise
        if self._detailed_log:
            logger.info(
                f"接口ASR响应：status={response.status_code} elapsed={time.time() - started_at:.2f}s "
                f"body={_log_preview(response.text)}"
            )
        else:
            logger.info(
                f"接口ASR响应：status={response.status_code} elapsed={time.time() - started_at:.2f}s "
                f"body_chars={len(response.text or '')}"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            status_code = response.status_code
            retryable = status_code == 429 or status_code >= 500
            raise AsrRequestError(
                f"接口ASR返回错误 {status_code}: {response.text[:500]}",
                retryable=retryable
            ) from err
        try:
            return response.json()
        except ValueError as err:
            raise RuntimeError(f"接口ASR返回非JSON响应: {response.text[:500]}") from err

    @staticmethod
    def __is_retryable_asr_error(err: Exception) -> bool:
        if isinstance(err, AsrRequestError):
            return err.retryable
        return isinstance(err, (httpx.TimeoutException, httpx.TransportError))

    def __wait_before_asr_retry(self, seconds: int, task: Optional[TaskItem], chunk_no: int, expected_chunks: int,
                                attempt: int, max_attempts: int, base_progress: float):
        deadline = time.time() + max(0, seconds)
        while time.time() < deadline:
            if self._event.is_set():
                raise UserInterruptException("用户中断当前任务")
            remaining = int(max(0, deadline - time.time()))
            self.__update_task_progress(
                task,
                base_progress,
                "提取并识别音频",
                f"第 {chunk_no}/{expected_chunks or '?'} 段ASR失败，"
                f"准备第 {attempt + 1}/{max_attempts} 次重试，等待 {remaining} 秒",
                force=True
            )
            time.sleep(min(2, max(0.2, remaining)))

    def __transcribe_audio_chunk_with_progress(self, audio_file: str, audio_lang: str, chunk_no: int,
                                               expected_chunks: int, task: Optional[TaskItem],
                                               base_progress: float, use_prompt: bool = True) -> dict:
        max_attempts = max(1, int(self._asr_request_retries or 1))
        last_error = None
        last_traceback = ""

        for attempt in range(1, max_attempts + 1):
            result = {}

            def worker():
                try:
                    result["response"] = self.__transcribe_audio_chunk(audio_file, audio_lang, use_prompt=use_prompt)
                except Exception as err:
                    result["error"] = err
                    result["traceback"] = traceback.format_exc()

            thread = threading.Thread(
                target=worker,
                name=f"autosubremoteasr-asr-{chunk_no}-{attempt}",
                daemon=True
            )
            started_at = time.time()
            forced_timeout = max(15, int(self._asr_request_timeout or 300) + 5)
            thread.start()
            while thread.is_alive():
                if self._event.is_set():
                    raise UserInterruptException("用户中断当前任务")
                elapsed = int(time.time() - started_at)
                if elapsed >= forced_timeout:
                    result["error"] = httpx.TimeoutException(
                        f"接口ASR请求超过 {self._asr_request_timeout} 秒，已放弃本次请求"
                    )
                    result["traceback"] = ""
                    logger.warn(
                        f"接口ASR第 {chunk_no}/{expected_chunks or '?'} 段第 {attempt}/{max_attempts} 次"
                        f"等待超过 {self._asr_request_timeout} 秒，强制进入重试"
                    )
                    break
                self.__update_task_progress(
                    task,
                    base_progress,
                    "提取并识别音频",
                    f"第 {chunk_no}/{expected_chunks or '?'} 段上传ASR中，"
                    f"第 {attempt}/{max_attempts} 次，已等待 {elapsed} 秒",
                    force=True
                )
                thread.join(timeout=2)

            if not result.get("error"):
                if attempt > 1:
                    logger.info(f"接口ASR第 {chunk_no}/{expected_chunks or '?'} 段第 {attempt} 次重试成功")
                return result.get("response") or {}

            last_error = result["error"]
            last_traceback = result.get("traceback") or ""
            retryable = self.__is_retryable_asr_error(last_error)
            if not retryable or attempt >= max_attempts:
                break

            delay = self._asr_retry_delays[min(attempt - 1, len(self._asr_retry_delays) - 1)]
            logger.warn(
                f"接口ASR第 {chunk_no}/{expected_chunks or '?'} 段第 {attempt}/{max_attempts} 次失败，"
                f"{delay} 秒后重试：{last_error}"
            )
            self.__wait_before_asr_retry(delay, task, chunk_no, expected_chunks, attempt, max_attempts, base_progress)

        if last_traceback:
            logger.error(last_traceback)
        if last_error and self.__is_retryable_asr_error(last_error):
            raise AsrTransientException(
                f"接口ASR第 {chunk_no}/{expected_chunks or '?'} 段网络异常，"
                f"已重试 {max_attempts} 次：{last_error}"
            ) from last_error
        if last_error:
            raise last_error
        return {}

    @staticmethod
    def __get_audio_file_duration(audio_file: str) -> float:
        meta = Ffmpeg().get_video_metadata(audio_file)
        try:
            return float((meta or {}).get("format", {}).get("duration") or 0)
        except Exception:
            return 0.0

    @staticmethod
    def __format_db_value(value: Optional[float]) -> str:
        if value is None:
            return "-"
        if not math.isfinite(float(value)):
            return str(value)
        return f"{value:.1f}dB"

    def __probe_audio_local_quality(self, sample_file: str) -> Tuple[bool, str]:
        ok, metrics, error = Ffmpeg().measure_audio_volume(
            sample_file,
            stop_event=self._event,
            threads=self._cpu_threads,
        )
        if not ok:
            logger.debug(f"全局语言探测本地音量检查失败，继续上传ASR判断：{error}")
            return True, "本地音量未知"

        mean_volume = self.__safe_float((metrics or {}).get("mean_volume"))
        max_volume = self.__safe_float((metrics or {}).get("max_volume"))
        detail = (
            f"mean={self.__format_db_value(mean_volume)} "
            f"max={self.__format_db_value(max_volume)}"
        )
        if max_volume is not None and max_volume <= -45:
            return False, f"本地音量过低 {detail}"
        if mean_volume is not None and max_volume is not None and mean_volume <= -55 and max_volume <= -35:
            return False, f"本地疑似静音 {detail}"
        return True, detail

    @staticmethod
    def __build_language_probe_offsets(duration: float, sample_count: int = 5,
                                       sample_seconds: int = 12) -> List[float]:
        try:
            duration = float(duration or 0)
        except Exception:
            duration = 0
        if duration <= 0:
            return []
        if duration <= sample_seconds + 6:
            return [0.0]

        trim = max(30.0, duration * 0.1)
        if duration - (trim * 2) <= sample_seconds:
            trim = max(0.0, (duration - sample_seconds) / 4)
        start_min = max(0.0, trim)
        start_max = max(start_min, duration - trim - sample_seconds)
        if start_max <= start_min:
            return [max(0.0, (duration - sample_seconds) / 2)]

        count = max(1, min(sample_count, int(sample_count or 5)))
        slot_width = (start_max - start_min) / count
        offsets = []
        for index in range(count):
            left = start_min + index * slot_width
            right = start_min + (index + 1) * slot_width
            right = min(right, start_max)
            if right <= left:
                offsets.append(round(left, 2))
            else:
                offsets.append(round(random.uniform(left, right), 2))
        return offsets

    @staticmethod
    def __safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def __clean_language_probe_text(text: str) -> str:
        text = re.sub(r"\s+", "", text or "")
        return re.sub(r"[，。！？、,.!?…~～ー\\/_\\-—\\[\\]（）()「」『』\"'`]+", "", text)

    @staticmethod
    def __probe_repetition_stats(text: str) -> Dict[str, float]:
        chars = [char for char in AutoSubRemoteAsr.__clean_language_probe_text(text) if not char.isdigit()]
        if not chars:
            return {"length": 0, "unique_ratio": 0.0, "max_char_ratio": 1.0, "max_run_ratio": 1.0}
        counts: Dict[str, int] = {}
        max_run = 1
        current_run = 1
        previous = None
        for char in chars:
            counts[char] = counts.get(char, 0) + 1
            if char == previous:
                current_run += 1
            else:
                current_run = 1
            previous = char
            max_run = max(max_run, current_run)
        length = len(chars)
        return {
            "length": length,
            "unique_ratio": len(counts) / length,
            "max_char_ratio": max(counts.values()) / length,
            "max_run_ratio": max_run / length,
        }

    @staticmethod
    def __language_script_score(text: str, language: str) -> float:
        chars = [char for char in AutoSubRemoteAsr.__clean_language_probe_text(text) if char.isalpha()]
        if not chars:
            return 0.0

        total = len(chars)
        latin = 0
        kana = 0
        cjk = 0
        hangul = 0
        for char in chars:
            code = ord(char)
            if ("A" <= char <= "Z") or ("a" <= char <= "z"):
                latin += 1
            elif 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
                kana += 1
            elif 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
                cjk += 1
            elif 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
                hangul += 1

        language = language or ""
        if language == "ja":
            kana_ratio = kana / total
            if kana_ratio >= 0.08:
                return min(1.0, (kana + cjk) / total)
            if kana > 0:
                return 0.65
            return 0.35 if cjk / total >= 0.5 else 0.0
        if language == "ko":
            return hangul / total
        if language == "zh":
            if kana or hangul:
                return 0.2
            return cjk / total
        if language == "en":
            return latin / total
        return max(latin, cjk, kana, hangul) / total

    def __score_language_probe_response(self, response: dict, language: str) -> Dict[str, Any]:
        text = (response or {}).get("text") or ""
        segments = (response or {}).get("segments") or []
        if not text and segments:
            text = " ".join((segment.get("text") or "") for segment in segments)

        avg_values = []
        compression_values = []
        no_speech_values = []
        for segment in segments:
            avg_logprob = self.__safe_float(segment.get("avg_logprob"))
            compression_ratio = self.__safe_float(segment.get("compression_ratio"))
            no_speech_prob = self.__safe_float(segment.get("no_speech_prob"))
            if avg_logprob is not None:
                avg_values.append(avg_logprob)
            if compression_ratio is not None:
                compression_values.append(compression_ratio)
            if no_speech_prob is not None:
                no_speech_values.append(no_speech_prob)

        avg_logprob = sum(avg_values) / len(avg_values) if avg_values else None
        compression_ratio = max(compression_values) if compression_values else None
        no_speech_prob = max(no_speech_values) if no_speech_values else None
        repeat_stats = self.__probe_repetition_stats(text)
        script_score = self.__language_script_score(text, language)

        reasons = []
        if not language:
            reasons.append("语言无效")
        if repeat_stats["length"] < 4:
            reasons.append("有效文本过短")
        if compression_ratio is not None and compression_ratio > 2.4:
            reasons.append(f"重复压缩比过高 {compression_ratio:.2f}")
        if avg_logprob is not None and avg_logprob < -1.15:
            reasons.append(f"置信度过低 {avg_logprob:.2f}")
        if no_speech_prob is not None and no_speech_prob > 0.75:
            reasons.append(f"静音概率过高 {no_speech_prob:.2f}")
        if repeat_stats["length"] >= 20 and (
                repeat_stats["max_char_ratio"] > 0.55
                or repeat_stats["max_run_ratio"] > 0.25
                or repeat_stats["unique_ratio"] < 0.08
        ):
            reasons.append("文本重复度过高")
        if language in {"ja", "ko", "zh", "en"} and script_score <= 0.05:
            reasons.append(f"字符集不匹配 {script_score:.2f}")

        score = 1.0
        if avg_logprob is not None:
            if avg_logprob >= -0.35:
                score *= 1.2
            elif avg_logprob < -0.8:
                score *= 0.55
            elif avg_logprob < -0.55:
                score *= 0.75
        if no_speech_prob is not None:
            if no_speech_prob > 0.55:
                score *= 0.45
            elif no_speech_prob > 0.35:
                score *= 0.75
        if compression_ratio is not None:
            if compression_ratio > 2.0:
                score *= 0.45
            elif compression_ratio > 1.6:
                score *= 0.75
        if repeat_stats["length"] < 12:
            score *= 0.55
        if repeat_stats["length"] >= 20 and repeat_stats["unique_ratio"] < 0.16:
            score *= 0.45
        if language in {"ja", "ko", "zh", "en"}:
            if script_score >= 0.6:
                score *= 1.2
            elif script_score < 0.25:
                score *= 0.35

        score = round(max(0.0, min(1.5, score)), 3)
        if score < 0.25:
            reasons.append(f"综合权重过低 {score:.2f}")

        return {
            "valid": not reasons,
            "score": score,
            "reason": "；".join(reasons),
            "text_len": repeat_stats["length"],
            "avg_logprob": avg_logprob,
            "compression_ratio": compression_ratio,
            "no_speech_prob": no_speech_prob,
            "script_score": script_score,
            "unique_ratio": repeat_stats["unique_ratio"],
            "max_char_ratio": repeat_stats["max_char_ratio"],
        }

    @staticmethod
    def __format_probe_metric(value: Optional[float]) -> str:
        return "-" if value is None else f"{value:.2f}"

    @staticmethod
    def __select_language_probe_winner(scores: Dict[str, float], counts: Dict[str, int],
                                       valid_count: int) -> Tuple[Optional[str], str]:
        if valid_count < 3:
            return None, f"有效样本不足 {valid_count}/3"
        total_score = sum(scores.values())
        if total_score <= 0:
            return None, "有效样本总分为0"
        ranking = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        language, score = ranking[0]
        second_score = ranking[1][1] if len(ranking) > 1 else 0.0
        share = score / total_score
        ratio = score / second_score if second_score > 0 else 99.0
        if counts.get(language, 0) < 2:
            return None, f"最高语言 {language} 仅 {counts.get(language, 0)} 个有效样本"
        if share < 0.60:
            return None, f"最高语言 {language} 得分占比不足 {share:.2f}"
        if second_score > 0 and ratio < 1.5 and score - second_score < 1.0:
            return None, f"最高语言 {language} 与第二名差距不足，ratio={ratio:.2f}"
        summary = ", ".join(
            f"{lang}:{scores[lang]:.2f}/{counts.get(lang, 0)}"
            for lang, _ in ranking
        )
        return language, f"score={summary}, share={share:.2f}, ratio={ratio:.2f}"

    def __detect_global_audio_language(self, video_file: str, audio_index: int, duration: float,
                                       audio_dir: str, task: Optional[TaskItem] = None) -> str:
        sample_seconds = 12
        sample_count = 5
        max_rounds = 3
        probe_dir = os.path.join(audio_dir, "language_probe")
        scores: Dict[str, float] = {}
        counts: Dict[str, int] = {}
        valid_details = []
        invalid_details = []
        total_samples = 0
        total_expected = sample_count * max_rounds

        cached_language = self.__get_cached_language_probe(video_file, audio_index)
        if cached_language:
            self.__update_task_progress(task, 24, "全局语言检测",
                                        f"复用缓存语言 {cached_language}，后续分段统一使用该语言", force=True)
            return cached_language

        for round_no in range(1, max_rounds + 1):
            offsets = self.__build_language_probe_offsets(
                duration,
                sample_count=sample_count,
                sample_seconds=sample_seconds
            )
            if not offsets:
                raise RuntimeError("auto模式全局语言探测失败：未读取到有效视频时长")

            logger.info(
                f"开始全局语言探测第 {round_no}/{max_rounds} 轮：避开开头结尾，"
                f"随机抽取 {len(offsets)} 个 {sample_seconds} 秒音频小段"
            )
            self.__update_task_progress(task, 24, "全局语言检测",
                                        f"第 {round_no}/{max_rounds} 轮随机抽取 {len(offsets)} 个音频小段",
                                        force=True)

            for index, offset in enumerate(offsets, 1):
                total_samples += 1
                if self._event.is_set():
                    raise UserInterruptException("用户中断当前任务")
                sample_file = os.path.join(probe_dir, f"probe_{total_samples:02d}.mp3")
                ok, error = Ffmpeg().extract_audio_sample_from_video(
                    video_file,
                    sample_file,
                    audio_index=audio_index,
                    start_seconds=offset,
                    duration_seconds=sample_seconds,
                    stop_event=self._event,
                    threads=self._cpu_threads,
                )
                if self._event.is_set():
                    raise UserInterruptException("用户中断当前任务")
                if not ok:
                    logger.warn(f"全局语言探测样本 {total_samples}/{total_expected} 提取失败：{error}")
                    continue
                try:
                    self.__validate_audio_chunk(sample_file, total_samples, total_expected, force=True)
                    usable, local_detail = self.__probe_audio_local_quality(sample_file)
                    if not usable:
                        invalid_details.append(f"{total_samples}:local:{local_detail}")
                        logger.warn(
                            f"全局语言探测样本 {total_samples}/{total_expected} 本地过滤跳过ASR："
                            f"offset={round(offset, 2)}s {local_detail}"
                        )
                        continue
                    self.__update_task_progress(task, 24, "全局语言检测",
                                                f"样本 {total_samples}/{total_expected} 上传ASR判断语言")
                    response = self.__transcribe_audio_chunk_with_progress(
                        sample_file,
                        "auto",
                        total_samples,
                        total_expected,
                        task,
                        24,
                        use_prompt=False
                    )
                    raw_language = response.get("language")
                    language = self.__normalize_language_code(raw_language, fallback="")
                    quality = self.__score_language_probe_response(response, language)
                    metric_text = (
                        f"score={quality['score']:.2f} text_len={quality['text_len']} "
                        f"avg={self.__format_probe_metric(quality['avg_logprob'])} "
                        f"comp={self.__format_probe_metric(quality['compression_ratio'])} "
                        f"nospeech={self.__format_probe_metric(quality['no_speech_prob'])} "
                        f"script={quality['script_score']:.2f} unique={quality['unique_ratio']:.2f}"
                    )
                    if language and quality["valid"]:
                        scores[language] = scores.get(language, 0.0) + quality["score"]
                        counts[language] = counts.get(language, 0) + 1
                        valid_details.append(f"{total_samples}:{language}/{quality['score']:.2f}")
                        logger.info(
                            f"全局语言探测样本 {total_samples}/{total_expected}：offset={round(offset, 2)}s "
                            f"language={raw_language} -> {language} local={local_detail} {metric_text}"
                        )
                    else:
                        reason = quality["reason"] or "语言无效"
                        invalid_details.append(f"{total_samples}:{raw_language or '-'}:{reason}")
                        logger.warn(
                            f"全局语言探测样本 {total_samples}/{total_expected} 无效："
                            f"offset={round(offset, 2)}s language={raw_language} -> {language or '-'} "
                            f"{metric_text} reason={reason}"
                        )

                    winner, reason = self.__select_language_probe_winner(scores, counts, len(valid_details))
                    if winner:
                        logger.info(
                            f"全局语言探测提前完成：language={winner}，{reason}，"
                            f"samples={total_samples}/{total_expected}，"
                            f"valid={', '.join(valid_details)}，invalid={', '.join(invalid_details[-6:])}"
                        )
                        self.__save_cached_language_probe(
                            video_file,
                            audio_index,
                            winner,
                            reason,
                            total_samples,
                            len(valid_details),
                        )
                        self.__update_task_progress(task, 24, "全局语言检测",
                                                    f"语言锁定为 {winner}，后续分段统一使用该语言", force=True)
                        return winner
                except Exception as err:
                    invalid_details.append(f"{total_samples}:error:{err}")
                    logger.warn(f"全局语言探测样本 {total_samples}/{total_expected} 失败：{err}")
                finally:
                    try:
                        os.remove(sample_file)
                    except Exception:
                        pass

            winner, reason = self.__select_language_probe_winner(scores, counts, len(valid_details))
            if winner:
                logger.info(
                    f"全局语言探测完成：language={winner}，{reason}，"
                    f"valid={', '.join(valid_details)}，invalid={', '.join(invalid_details[-6:])}"
                )
                self.__save_cached_language_probe(
                    video_file,
                    audio_index,
                    winner,
                    reason,
                    total_samples,
                    len(valid_details),
                )
                self.__update_task_progress(task, 24, "全局语言检测",
                                            f"语言锁定为 {winner}，后续分段统一使用该语言", force=True)
                return winner

            logger.warn(
                f"全局语言探测第 {round_no}/{max_rounds} 轮未锁定：{reason}，"
                f"valid={', '.join(valid_details) or '-'}"
            )
            self.__update_task_progress(task, 24, "全局语言检测",
                                        f"第 {round_no}/{max_rounds} 轮未锁定：{reason}", force=True)

        score_summary = ", ".join(
            f"{lang}:{scores[lang]:.2f}/{counts.get(lang, 0)}"
            for lang, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        ) or "-"
        raise RuntimeError(
            f"auto模式全局语言探测失败：最多 {total_samples} 个随机样本仍无法稳定判断，"
            f"score={score_summary}"
        )

    @staticmethod
    def __get_chunk_no(chunk_file: str) -> int:
        try:
            return int(os.path.splitext(os.path.basename(chunk_file))[0].split("_")[-1]) + 1
        except Exception:
            return 1

    def __should_check_asr_chunk(self, chunk_no: int, expected_chunks: int) -> bool:
        if chunk_no == 1 or (expected_chunks and chunk_no >= expected_chunks):
            return True
        if expected_chunks and expected_chunks <= 3:
            return True
        return random.random() < self._asr_random_check_rate

    def __validate_audio_chunk(self, chunk_file: str, chunk_no: int, expected_chunks: int, force: bool = False):
        if not os.path.exists(chunk_file):
            raise RuntimeError(f"音频分段不存在：第 {chunk_no} 段")
        file_size = os.path.getsize(chunk_file)
        if file_size <= 128:
            raise RuntimeError(f"音频分段过小：第 {chunk_no} 段，{file_size} bytes")

        if not force:
            return

        duration = self.__get_audio_file_duration(chunk_file)
        if duration <= 0:
            raise RuntimeError(f"音频分段不可读：第 {chunk_no} 段")
        logger.info(
            f"接口ASR随机检查通过：第 {chunk_no}/{expected_chunks or '?'} 段，"
            f"音频 {round(duration, 2)} 秒，{round(file_size / 1024, 1)} KB"
        )

    @staticmethod
    def __validate_asr_response(response: dict, chunk_no: int, expected_chunks: int, checked: bool = False):
        if not isinstance(response, dict):
            raise RuntimeError(f"接口ASR响应格式异常：第 {chunk_no} 段")
        segments = response.get("segments")
        if segments is not None and not isinstance(segments, list):
            raise RuntimeError(f"接口ASR segments 格式异常：第 {chunk_no} 段")
        if not checked:
            return

        bad_segments = 0
        last_start = -1.0
        for segment in segments or []:
            try:
                start = float(segment.get("start") or 0)
                end = float(segment.get("end") or 0)
            except Exception:
                bad_segments += 1
                continue
            if start < last_start or end <= start:
                bad_segments += 1
            last_start = start
        if bad_segments:
            raise RuntimeError(f"接口ASR时间轴异常：第 {chunk_no} 段，异常 segments {bad_segments}")
        logger.info(
            f"接口ASR随机检查通过：第 {chunk_no}/{expected_chunks or '?'} 段，"
            f"segments={len(segments or [])}，text={len(response.get('text') or '')}"
        )

    def __append_asr_response_subs(self, subs: List[srt.Subtitle], response: dict, offset: float, chunk_file: str):
        segments = response.get("segments") or []
        for segment in segments:
            text = (segment.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(segment.get("start") or 0) + offset
                end = float(segment.get("end") or 0) + offset
            except Exception:
                continue
            if end <= start:
                end = start + 0.5
            subs.append(srt.Subtitle(
                index=len(subs) + 1,
                start=timedelta(seconds=max(0, start)),
                end=timedelta(seconds=max(0, end)),
                content=text
            ))

        if segments:
            return

        text = (response.get("text") or "").strip()
        if not text:
            return
        duration = float(response.get("duration") or 0)
        if duration <= 0 and chunk_file and os.path.exists(chunk_file):
            duration = self.__get_audio_file_duration(chunk_file)
        if duration <= 0:
            duration = self._asr_chunk_seconds
        subs.append(srt.Subtitle(
            index=len(subs) + 1,
            start=timedelta(seconds=max(0, offset)),
            end=timedelta(seconds=max(0, offset + duration)),
            content=text
        ))

    def __do_speech_recognition(self, video_file: str, audio_index: int, audio_lang: str, audio_dir: str,
                                expected_chunks: int, task: Optional[TaskItem] = None, duration: float = 0):
        """
        流水线调用远程语音识别接口生成字幕。
        :param audio_lang:
        :return:
        """
        lang = audio_lang
        try:
            if lang == "auto":
                lang = self.__detect_global_audio_language(
                    video_file,
                    audio_index,
                    duration,
                    audio_dir,
                    task
                )
                logger.info(f"auto模式全局语言探测已锁定 ASR 语言：{lang}")
            if self.__is_target_language(lang):
                logger.info(f"音轨语言已匹配目标翻译语言（{lang}），跳过ASR分段识别和字幕文件生成")
                self.__clear_asr_checkpoint(task, video_file)
                self.__update_task_progress(
                    task,
                    98,
                    "同语言跳过",
                    f"音轨语言已匹配目标翻译语言：{lang}，无需生成字幕",
                    force=True,
                )
                return True, lang, []
            self.__update_task_progress(task, 34, "接口语音识别",
                                        f"模型：{self._asr_api_model}，语言：{lang}，预计 {expected_chunks} 段",
                                        force=True)
            subs = []
            checkpoint = self.__get_asr_checkpoint(task, video_file, expected_chunks, lang)
            checkpoint_chunks = (checkpoint or {}).get("chunks") or {}
            detected_lang = lang
            processed_chunks = 0

            def handle_chunk(chunk_file: str):
                nonlocal detected_lang, processed_chunks
                if self._event.is_set():
                    logger.info("接口ASR服务停止")
                    raise UserInterruptException("用户中断当前任务")
                chunk_no = self.__get_chunk_no(chunk_file)
                checked = self.__should_check_asr_chunk(chunk_no, expected_chunks)
                self.__validate_audio_chunk(chunk_file, chunk_no, expected_chunks, force=checked)

                cached_response = checkpoint_chunks.get(str(chunk_no))
                if cached_response is not None:
                    try:
                        response = copy.deepcopy(cached_response)
                        self.__validate_asr_response(response, chunk_no, expected_chunks, checked=checked)
                        offset = (chunk_no - 1) * self._asr_chunk_seconds
                        self.__append_asr_response_subs(subs, response, offset, chunk_file)
                        processed_chunks += 1
                        try:
                            os.remove(chunk_file)
                        except Exception as err:
                            logger.warn(f"删除临时音频分段失败：{chunk_file} - {err}")
                        percent = self.__scale_progress(24, 72, processed_chunks, expected_chunks)
                        self.__update_task_progress(task, percent, "提取并识别音频",
                                                    f"断点恢复 {processed_chunks}/{expected_chunks or '?'} 段，"
                                                    f"已识别 {len(subs)} 条")
                        return
                    except Exception as err:
                        logger.warn(f"接口ASR断点第 {chunk_no}/{expected_chunks or '?'} 段不可用，重新识别：{err}")

                percent = self.__scale_progress(24, 72, processed_chunks, expected_chunks)
                self.__update_task_progress(task, percent, "提取并识别音频",
                                            f"第 {chunk_no}/{expected_chunks or '?'} 段正在上传ASR")
                try:
                    response = self.__transcribe_audio_chunk_with_progress(
                        chunk_file,
                        lang,
                        chunk_no,
                        expected_chunks,
                        task,
                        percent
                    )
                except AsrTransientException as err:
                    self.__mark_waiting_file(task, str(err))
                    raise
                self.__validate_asr_response(response, chunk_no, expected_chunks, checked=checked)
                self.__save_asr_chunk_checkpoint(task, video_file, expected_chunks, lang,
                                                 chunk_no, response, detected_lang)
                offset = (chunk_no - 1) * self._asr_chunk_seconds
                self.__append_asr_response_subs(subs, response, offset, chunk_file)
                processed_chunks += 1
                try:
                    os.remove(chunk_file)
                except Exception as err:
                    logger.warn(f"删除临时音频分段失败：{chunk_file} - {err}")
                percent = self.__scale_progress(24, 72, processed_chunks, expected_chunks)
                self.__update_task_progress(task, percent, "提取并识别音频",
                                            f"已处理 {processed_chunks}/{expected_chunks or '?'} 段，"
                                            f"已识别 {len(subs)} 条")

            ok, chunk_count, error = Ffmpeg().stream_audio_chunks_from_video(
                video_file,
                audio_dir,
                audio_index,
                segment_seconds=self._asr_chunk_seconds,
                stop_event=self._event,
                threads=self._cpu_threads,
                chunk_callback=handle_chunk
            )
            if self._event.is_set():
                raise UserInterruptException("用户中断当前任务")
            if not ok:
                logger.error(f"接口ASR音频流水线失败：{error}")
                if task and task.status == TaskStatus.WAITING_FILE:
                    return False, None, []
                if error and not error.startswith("处理音频分段失败"):
                    self.__mark_waiting_file(task, error or "提取音频失败，可能文件仍未完整")
                return False, None, []
            if chunk_count <= 0:
                logger.error("接口ASR没有可识别的音频分段")
                return False, None, []

            final_lang = self.__normalize_language_code(lang, fallback="und")
            if not subs:
                logger.info("音频文件中未检测到任何语言内容，生成空字幕文件以避免重复处理")
            self.__update_task_progress(task, 72, "语音识别完成", f"识别到 {len(subs)} 条字幕", force=True)
            self.__clear_asr_checkpoint(task, video_file)
            logger.info("接口音轨转字幕完成")
            return True, final_lang, subs
        except UserInterruptException:
            raise
        except Exception as e:
            logger.error(f"接口ASR处理异常：{e}")
            logger.error(traceback.format_exc())
            return False, None, []

    def __generate_subtitle(self, video_file, subtitle_file, enable_asr=True, task: Optional[TaskItem] = None):
        """
        生成字幕
        :param video_file: 视频文件
        :param subtitle_file: 字幕文件, 不包含后缀
        :return: 生成成功返回True，字幕语言,字幕路径，否则返回False, None, None
        """
        # 获取文件元数据
        self.__update_task_progress(task, 6, "读取媒体信息", "正在读取视频元数据", force=True)
        video_meta = Ffmpeg().get_video_metadata(video_file, stop_event=self._event)
        if self._event.is_set():
            raise UserInterruptException("用户中断当前任务")
        if not video_meta:
            logger.error(f"获取视频文件元数据失败，跳过后续处理")
            self.__mark_waiting_file(task, "获取视频文件元数据失败，可能文件仍未完整")
            return False, None, None
        # 获取字幕语言偏好
        if self._translate_preference == "english_only":
            prefer_subtitle_langs = ['en', 'eng']
            strict = True
        elif self._translate_preference == "english_first":
            prefer_subtitle_langs = ['en', 'eng']
            strict = False
        else:  # self.translate_preference == "origin_first"
            prefer_subtitle_langs = None
            strict = False

        # 从视频文件音轨获取语言信息
        ret, audio_index, audio_lang = self.__get_video_prefer_audio(video_meta, prefer_lang=prefer_subtitle_langs)
        if not ret:
            logger.info(f"字幕源偏好：{self._translate_preference} 获取音轨元数据失败")
            self.__update_task_progress(task, 10, "处理失败", "获取音轨元数据失败", force=True)
            return False, None, None

        # 如果开启了自动语言检测，直接设置为auto，跳过metadata的语言信息
        if self._auto_detect_language:
            logger.info("已开启ASR自动检测语言，将先随机抽取全局音频样本锁定语言")
            audio_lang = 'auto'
        elif not iso639.find(audio_lang) or not iso639.to_iso639_1(audio_lang):
            logger.info(f"字幕源偏好：{self._translate_preference} 未从音轨元数据中获取到语言信息")
            audio_lang = 'auto'

        # 当字幕源偏好为origin_first时，优先使用音轨语言
        if self._translate_preference == "origin_first":
            prefer_subtitle_langs = ['en', 'eng'] if audio_lang == 'auto' else [audio_lang,
                                                                                iso639.to_iso639_1(audio_lang)]
        # 获取外挂字幕
        self.__update_task_progress(task, 12, "匹配字幕", "正在匹配外挂字幕", force=True)
        logger.info(f"使用 {prefer_subtitle_langs} 匹配已有外挂字幕文件 ...")
        external_sub_exist, external_sub_lang, exist_sub_name = self.__external_subtitle_exists(video_file,
                                                                                                prefer_subtitle_langs,
                                                                                                only_srt=True,
                                                                                                strict=strict)
        # 获取内嵌字幕
        self.__update_task_progress(task, 16, "匹配字幕", "正在匹配内嵌字幕", force=True)
        logger.info(f"使用 {prefer_subtitle_langs} 匹配内嵌字幕文件 ...")
        inner_sub_exist, subtitle_index, inner_sub_lang, = self.__get_video_prefer_subtitle(video_meta,
                                                                                            prefer_subtitle_langs,
                                                                                            strict=strict)

        # 优先返回符合语言要求的外部字幕
        def get_sub_path():
            video_dir, _ = os.path.split(video_file)
            return os.path.join(video_dir, exist_sub_name)

        extract_subtitle = False
        if self._translate_preference == "english_only":
            if external_sub_exist:
                logger.info(f"字幕源偏好：{self._translate_preference} 外挂字幕存在，字幕语言 {external_sub_lang}")
                self.__update_task_progress(task, 65, "使用已有字幕", f"外挂字幕语言：{external_sub_lang}", force=True)
                return True, iso639.to_iso639_1(external_sub_lang), get_sub_path()
            elif inner_sub_exist:
                logger.info(f"字幕源偏好：{self._translate_preference} 内嵌字幕存在，字幕语言 {inner_sub_lang}")
                extract_subtitle = True
            else:
                logger.info(f"字幕源偏好：{self._translate_preference} 未匹配到外挂或内嵌字幕,需要使用asr提取")
        else:  # english_first/origin_first
            if external_sub_exist and external_sub_lang in prefer_subtitle_langs:
                logger.info(f"字幕源偏好：{self._translate_preference} 外挂字幕存在，字幕语言 {external_sub_lang}")
                self.__update_task_progress(task, 65, "使用已有字幕", f"外挂字幕语言：{external_sub_lang}", force=True)
                return True, iso639.to_iso639_1(external_sub_lang), get_sub_path()
            elif inner_sub_exist and inner_sub_lang in prefer_subtitle_langs:
                logger.info(f"字幕源偏好：{self._translate_preference} 内嵌字幕存在，字幕语言 {inner_sub_lang}")
                extract_subtitle = True
            elif external_sub_exist:
                logger.info(f"字幕源偏好：{self._translate_preference} 外挂字幕存在，字幕语言 {external_sub_lang}")
                self.__update_task_progress(task, 65, "使用已有字幕", f"外挂字幕语言：{external_sub_lang}", force=True)
                return True, iso639.to_iso639_1(external_sub_lang), get_sub_path()
            elif inner_sub_exist:
                logger.info(f"字幕源偏好：{self._translate_preference} 内嵌字幕存在，字幕语言 {inner_sub_lang}")
                extract_subtitle = True
            else:
                logger.info(f"字幕源偏好：{self._translate_preference} 未匹配到外挂或内嵌字幕,需要使用asr提取")
        # 提取内嵌字幕
        if extract_subtitle:
            inner_sub_lang = iso639.to_iso639_1(inner_sub_lang) \
                if (inner_sub_lang and iso639.find(inner_sub_lang) and iso639.to_iso639_1(inner_sub_lang)) else 'und'
            extracted_sub_path = f"{subtitle_file}.{inner_sub_lang}.srt"
            self.__update_task_progress(task, 28, "提取内嵌字幕", f"字幕语言：{inner_sub_lang}", force=True)
            if not Ffmpeg().extract_subtitle_from_video(video_file, extracted_sub_path, subtitle_index,
                                                        stop_event=self._event, threads=self._cpu_threads):
                if self._event.is_set():
                    raise UserInterruptException("用户中断当前任务")
                self.__mark_waiting_file(task, "提取内嵌字幕失败，可能文件仍未完整")
                return False, None, None
            logger.info(f"提取字幕完成：{extracted_sub_path}")
            self.__update_task_progress(task, 65, "提取内嵌字幕完成", f"字幕语言：{inner_sub_lang}", force=True)
            return True, inner_sub_lang, extracted_sub_path
        # 使用接口ASR音轨识别字幕
        if audio_lang != 'auto':
            audio_lang = self.__normalize_language_code(audio_lang, fallback='auto')

        if not enable_asr:
            logger.info(f"未开启语音识别，且无已有字幕文件，跳过后续处理")
            self.__update_task_progress(task, 20, "处理失败", "未开启语音识别且无可用字幕", force=True)
            return False, None, None

        with tempfile.TemporaryDirectory(prefix='autosubremoteasr-') as audio_dir:
            duration = self.__get_video_duration(video_meta)
            expected_chunks = max(1, math.ceil(duration / self._asr_chunk_seconds)) if duration else 0
            logger.info(
                f"开始接口ASR流水线，语言 {audio_lang}，模型 {self._asr_api_model}，"
                f"分段 {self._asr_chunk_minutes} 分钟，预计 {expected_chunks or '?'} 段"
            )
            self.__update_task_progress(task, 24, "提取并识别音频",
                                        f"按 {self._asr_chunk_minutes} 分钟分段，生成后立即识别", force=True)
            ret, lang, subs = self.__do_speech_recognition(
                video_file,
                audio_index,
                audio_lang,
                audio_dir,
                expected_chunks,
                task,
                duration=duration
            )
            if ret:
                if self.__is_target_language(lang):
                    logger.info(f"原始语言已匹配目标翻译语言（{lang}），不保存原文字幕文件：{subtitle_file}.{lang}.srt")
                    self.__update_task_progress(task, 98, "同语言跳过", f"原始语言已匹配目标翻译语言：{lang}", force=True)
                    return True, lang, None
                logger.info(f"生成字幕成功，原始语言：{lang}")
                gen_subtitle_path = Path(f"{subtitle_file}.{lang}.srt")
                self.__save_srt(gen_subtitle_path, subs)
                logger.info(f"保存字幕文件：{gen_subtitle_path}")
                self.__update_task_progress(task, 74, "保存字幕", f"字幕语言：{lang}", force=True)
                return ret, lang, gen_subtitle_path
            else:
                logger.error("生成字幕失败")
                return False, None, None

    @staticmethod
    def __get_library_files(in_path, exclude_path=None):
        """
        获取目录媒体文件列表
        """
        exclude_paths = AutoSubRemoteAsr.__normalize_path_list(exclude_path)
        if not os.path.isdir(in_path):
            if AutoSubRemoteAsr.__path_matches_excludes(in_path, exclude_paths):
                return
            yield in_path
            return

        for root, dirs, files in os.walk(in_path):
            if AutoSubRemoteAsr.__path_matches_excludes(root, exclude_paths):
                dirs[:] = []
                continue
            dirs[:] = [
                dirname for dirname in dirs
                if not AutoSubRemoteAsr.__path_matches_excludes(os.path.join(root, dirname), exclude_paths)
            ]

            for file in files:
                cur_path = os.path.join(root, file)
                if AutoSubRemoteAsr.__path_matches_excludes(cur_path, exclude_paths):
                    continue
                # 检查后缀
                if os.path.splitext(file)[-1].lower() in settings.RMT_MEDIAEXT:
                    yield cur_path

    @staticmethod
    def __load_srt(file_path):
        """
        加载字幕文件
        :param file_path: 字幕文件路径
        :return:
        """
        with open(file_path, 'r', encoding="utf8") as f:
            srt_text = f.read()
        return list(srt.parse(srt_text))

    @staticmethod
    def __save_srt(file_path, srt_data):
        """
        保存字幕文件
        :param file_path: 字幕文件路径
        :param srt_data: 字幕数据
        :return:
        """
        with open(file_path, 'w', encoding="utf8") as f:
            f.write(srt.compose(srt_data))

    def __merge_srt(self, subtitle_data):
        """
        合并整句字幕
        :param subtitle_data:
        :return:
        """
        subtitle_data = copy.deepcopy(subtitle_data)
        # 合并字幕
        merged_subtitle = []
        sentence_end = True
        end_tokens = ['.', '!', '?', '。', '！', '？', '。"', '！"', '？"', '."', '!"', '?"']
        for index, item in enumerate(subtitle_data):
            # 当前字幕先将多行合并为一行，再去除首尾空格
            content = item.content.replace('\n', ' ').strip()
            # 去除html标签
            parse = etree.HTML(content)
            if parse is not None:
                content = parse.xpath('string(.)')
            if content == '':
                continue
            item.content = content

            # 背景音等字幕，跳过
            if self.__is_noisy_subtitle(content):
                merged_subtitle.append(item)
                sentence_end = True
                continue

            if not merged_subtitle or sentence_end:
                merged_subtitle.append(item)
            elif not sentence_end:
                merged_subtitle[-1].content = f"{merged_subtitle[-1].content} {content}"
                merged_subtitle[-1].end = item.end

            # 如果当前字幕内容以标志符结尾，则设置语句已经终结
            if content.endswith(tuple(end_tokens)):
                sentence_end = True
            # 如果上句字幕超过一定长度，则设置语句已经终结
            elif len(merged_subtitle[-1].content) > 80:
                sentence_end = True
            else:
                sentence_end = False

        return merged_subtitle

    @staticmethod
    def __get_video_prefer_audio(video_meta, prefer_lang=None):
        """
        获取视频的首选音轨，如果有多音轨， 优先指定语言音轨，否则获取默认音轨
        :param video_meta
        :return:
        """
        if type(prefer_lang) == str and prefer_lang:
            prefer_lang = [prefer_lang]

        # 获取首选音轨
        audio_lang = None
        audio_index = None
        audio_stream = filter(lambda x: x.get('codec_type') == 'audio', video_meta.get('streams', []))
        for index, stream in enumerate(audio_stream):
            if audio_index is None:
                audio_index = index
                audio_lang = stream.get('tags', {}).get('language', 'und')
            # 获取默认音轨
            if stream.get('disposition', {}).get('default'):
                audio_index = index
                audio_lang = stream.get('tags', {}).get('language', 'und')
            # 获取指定语言音轨
            if prefer_lang and stream.get('tags', {}).get('language') in prefer_lang:
                audio_index = index
                audio_lang = stream.get('tags', {}).get('language', 'und')
                break

        # 如果没有音轨， 则不处理
        if audio_index is None:
            logger.warn(f"没有音轨，不进行处理")
            return False, None, None

        logger.info(f"选中音轨信息：{audio_index}, {audio_lang}")
        return True, audio_index, audio_lang

    @staticmethod
    def __get_video_prefer_subtitle(video_meta, prefer_lang=None, strict=False, only_srt=True):
        """
        获取视频的首选字幕。优先级：1.字幕为偏好语言 2.默认字幕 3.第一个字幕
        :param video_meta: 视频元数据
        :param prefer_lang: 字幕偏好语言
        :param strict: 是否严格模式。如果指定了偏好语言，严格模式下必须返回偏好语言的字幕。
        :return: (是否命中字幕，字幕index，字幕语言)
        """
        # from https://wiki.videolan.org/Subtitles_codecs/
        """
        https://trac.ffmpeg.org/wiki/ExtractSubtitles
        ffmpeg -codecs | grep subtitle
         DES... ass                  ASS (Advanced SSA) subtitle (decoders: ssa ass ) (encoders: ssa ass )
         DES... dvb_subtitle         DVB subtitles (decoders: dvbsub ) (encoders: dvbsub )
         DES... dvd_subtitle         DVD subtitles (decoders: dvdsub ) (encoders: dvdsub )
         D.S... hdmv_pgs_subtitle    HDMV Presentation Graphic Stream subtitles (decoders: pgssub )
         ..S... hdmv_text_subtitle   HDMV Text subtitle
         D.S... jacosub              JACOsub subtitle
         D.S... microdvd             MicroDVD subtitle
         D.S... mpl2                 MPL2 subtitle
         D.S... pjs                  PJS (Phoenix Japanimation Society) subtitle
         D.S... realtext             RealText subtitle
         D.S... sami                 SAMI subtitle
         ..S... srt                  SubRip subtitle with embedded timing
         ..S... ssa                  SSA (SubStation Alpha) subtitle
         D.S... stl                  Spruce subtitle format
         DES... subrip               SubRip subtitle (decoders: srt subrip ) (encoders: srt subrip )
         D.S... subviewer            SubViewer subtitle
         D.S... subviewer1           SubViewer v1 subtitle
         D.S... vplayer              VPlayer subtitle
         DES... webvtt               WebVTT subtitle
        """
        image_based_subtitle_codecs = (
            'dvd_subtitle',
            'dvb_subtitle',
            'hdmv_pgs_subtitle',
        )

        if prefer_lang is str and prefer_lang:
            prefer_lang = [prefer_lang]

        # 获取首选字幕
        subtitle_lang = None
        subtitle_index = None
        subtitle_score = 0
        subtitle_stream = filter(lambda x: x.get('codec_type') == 'subtitle', video_meta.get('streams', []))
        for index, stream in enumerate(subtitle_stream):
            # 如果是强制字幕，则跳过
            if stream.get('disposition', {}).get('forced'):
                continue
            # image-based 字幕，跳过
            if only_srt and (
                    'width' in stream
                    or stream.get('codec_name') in image_based_subtitle_codecs
            ):
                continue
            cur_is_default = stream.get('disposition', {}).get('default')
            cur_lang = stream.get('tags', {}).get('language')
            # 计算当前字幕得分：1.字幕为偏好语言*4 2.默认字幕*2 3.第一个字幕*1
            cur_score = 0
            if prefer_lang and cur_lang in prefer_lang:
                cur_score += 4
            if cur_is_default:
                cur_score += 2
            if subtitle_index is None:
                cur_score += 1
                # 第一个字幕初始化为默认字幕
                subtitle_lang, subtitle_index, subtitle_score = cur_lang, index, cur_score
            if cur_score > subtitle_score:
                subtitle_lang, subtitle_index, subtitle_score = cur_lang, index, cur_score

        # 未找到字幕
        if subtitle_index is None:
            logger.debug(f"没有内嵌字幕")
            return False, None, None
        if strict and prefer_lang and subtitle_lang not in prefer_lang:
            logger.warn(f"严格模式,没有偏好语言的字幕")
            return False, None, None
        logger.debug(f"命中内嵌字幕信息：{subtitle_index}, {subtitle_lang}, score:{subtitle_score}")
        return True, subtitle_index, subtitle_lang

    @staticmethod
    def __is_noisy_subtitle(content):
        """
        判断是否为背景音等字幕
        :param content:
        :return:
        """
        noisy_tokens = [('(', ')'), ('[', ']'), ('{', '}'), ('【', '】'), ('♪', '♪'), ('♫', '♫'), ('♪♪', '♪♪')]
        return any(content.startswith(t[0]) and content.endswith(t[1]) for t in noisy_tokens)

    def __get_context(self, all_subs: list, target_indices: List[int], is_batch: bool) -> str:
        """通用上下文获取方法"""
        min_idx = max(0, min(target_indices) - self._context_window)
        max_idx = min(len(all_subs) - 1, max(target_indices) + self._context_window) if is_batch else min(
            target_indices)

        context = []
        for idx in range(min_idx, max_idx + 1):
            status = "[待译]" if idx in target_indices else ""
            content = all_subs[idx].content.replace('\n', ' ').strip()
            context.append(f"{status}{content}")

        return "\n".join(context)

    def __process_items(self, all_subs: list, items: list, stats: dict) -> list:
        """统一处理入口（支持批量和单条）"""
        if self._enable_batch and len(items) > 1:
            return self.__process_batch(all_subs, items, stats)
        return [self.__process_single(all_subs, item, stats) for item in items]

    def __run_translate_request(self, request_func):
        """执行一次翻译接口请求，超时只约束本次 HTTP 请求。"""
        if self._event.is_set():
            raise UserInterruptException("用户中断当前任务")

        result = {}
        request_id = uuid4().hex[:8]
        lock_wait_started = time.time()

        def worker():
            try:
                result["value"] = request_func()
            except Exception as err:
                result["error"] = err
                result["traceback"] = traceback.format_exc()

        logger.info(f"接口翻译准备请求：request_id={request_id} 等待并发锁")
        with self._translate_lock:
            logger.info(
                f"接口翻译获得并发锁：request_id={request_id} "
                f"wait={time.time() - lock_wait_started:.2f}s"
            )
            thread = threading.Thread(
                target=worker,
                name="autosubremoteasr-translate-request",
                daemon=True,
            )
            started_at = time.time()
            forced_timeout = max(15, int(self._translate_request_timeout or 60) + 5)
            thread.start()
            while thread.is_alive():
                if self._event.is_set():
                    raise UserInterruptException("用户中断当前任务")
                elapsed = int(time.time() - started_at)
                if elapsed >= forced_timeout:
                    logger.warn(f"翻译请求等待超过 {self._translate_request_timeout} 秒，强制放弃本次请求")
                    raise httpx.TimeoutException(
                        f"翻译请求超过 {self._translate_request_timeout} 秒，已放弃本次请求"
                    )
                thread.join(timeout=1)
            logger.info(
                f"接口翻译请求线程结束：request_id={request_id} "
                f"elapsed={time.time() - started_at:.2f}s"
            )

        if result.get("error"):
            raise result["error"]
        if self._event.is_set():
            raise UserInterruptException("用户中断当前任务")
        return result.get("value")

    def __sleep_for_translate_retry(self, seconds: float):
        end_time = time.time() + max(0.0, seconds)
        while time.time() < end_time:
            if self._event.is_set():
                raise UserInterruptException("用户中断当前任务")
            time.sleep(min(0.5, max(0.05, end_time - time.time())))

    def __run_translate_with_retries(self, request_func, label: str):
        max_attempts = max(1, int(self._max_retries or 0) + 1)
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                return self.__run_translate_request(request_func)
            except UserInterruptException:
                raise
            except Exception as err:
                last_error = err
                if attempt >= max_attempts:
                    raise
                sleep_time = (2 ** (attempt - 1)) + random.uniform(0.1, 0.9)
                logger.warn(
                    f"{label}请求失败（第 {attempt}/{max_attempts} 次）：{err}，"
                    f"{sleep_time:.1f} 秒后重试"
                )
                self.__sleep_for_translate_retry(sleep_time)
        if last_error:
            raise last_error
        raise RuntimeError(f"{label}请求失败")

    def __translate_to_zh(self, text: str, context: str = None) -> str:
        return self.__run_translate_with_retries(
            lambda: self._openai.translate_to_zh(text, context, max_retries=0),
            "单行翻译"
        )

    def __translate_subtitle_items_to_zh(self, items: List[dict], context: str = None):
        return self.__run_translate_with_retries(
            lambda: self._openai.translate_subtitle_items_to_zh(items, context, max_retries=0),
            "批量字幕翻译"
        )

    @staticmethod
    def __clean_json_response(text: str) -> str:
        text = (text or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    @classmethod
    def __load_json_response(cls, text: str):
        text = cls.__clean_json_response(text)
        try:
            return json.loads(text)
        except Exception:
            pass

        candidates = []
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start >= 0 and end > start:
                candidates.append(text[start:end + 1])
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except Exception:
                continue
        raise ValueError("模型未返回可解析的JSON")

    @staticmethod
    def __parse_numbered_translation_lines(text: str, expected_count: int) -> List[str]:
        translations = {}
        for line in (text or "").splitlines():
            match = re.match(r"^\s*(\d+)\s*[\.\):：、-]\s*(.+?)\s*$", line)
            if not match:
                continue
            index = int(match.group(1))
            if 1 <= index <= expected_count:
                translations[index] = match.group(2).strip()
        if len(translations) != expected_count:
            raise ValueError(f"编号行数不匹配 {len(translations)}/{expected_count}")
        return [translations[index] for index in range(1, expected_count + 1)]

    def __parse_batch_translation_response(self, result: str, expected_count: int) -> List[str]:
        try:
            data = self.__load_json_response(result)
        except Exception:
            return self.__parse_numbered_translation_lines(result, expected_count)

        if isinstance(data, dict):
            for key in ("items", "translations", "results", "data"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                translations = {}
                for key, value in data.items():
                    try:
                        index = int(key)
                    except Exception:
                        continue
                    if isinstance(value, dict):
                        value = value.get("text") or value.get("translation") or value.get("zh")
                    if isinstance(value, str) and value.strip():
                        translations[index] = value.strip()
                if len(translations) == expected_count:
                    return [translations[index] for index in range(1, expected_count + 1)]
                raise ValueError(f"JSON编号不匹配 {len(translations)}/{expected_count}")

        if not isinstance(data, list):
            raise ValueError("JSON结果不是数组")

        translations = {}
        sequential_values = []
        for entry in data:
            if isinstance(entry, dict):
                raw_id = entry.get("id") or entry.get("index") or entry.get("line") or entry.get("no")
                text = entry.get("text") or entry.get("translation") or entry.get("zh") or entry.get("content")
                try:
                    index = int(raw_id)
                except Exception:
                    index = None
                if index and isinstance(text, str) and text.strip():
                    translations[index] = text.strip()
                elif isinstance(text, str) and text.strip():
                    sequential_values.append(text.strip())
            elif isinstance(entry, str) and entry.strip():
                sequential_values.append(entry.strip())

        if len(translations) == expected_count:
            return [translations[index] for index in range(1, expected_count + 1)]
        if not translations and len(sequential_values) == expected_count:
            return sequential_values
        raise ValueError(f"JSON条目数不匹配 {len(translations) or len(sequential_values)}/{expected_count}")

    def __process_batch(self, all_subs: list, batch: list, stats: dict) -> list:
        """批量处理逻辑"""
        indices = [all_subs.index(item) for item in batch]
        context = self.__get_context(all_subs, indices, is_batch=True) if self._context_window > 0 else None
        batch_items = [
            {
                "id": index + 1,
                "text": item.content.replace("\n", " ").strip()
            }
            for index, item in enumerate(batch)
        ]

        try:
            ret, result = self.__translate_subtitle_items_to_zh(batch_items, context)
            if not ret:
                raise Exception(result)
            translated = self.__parse_batch_translation_response(result, len(batch))

            for item, trans in zip(batch, translated):
                item.content = f"{trans}\n{item.content}"
            stats['batch_success'] += len(batch)
            return batch
        except UserInterruptException:
            raise
        except Exception as e:
            stats['batch_fail'] += 1
            if len(batch) <= 1:
                logger.warning(f"批次翻译失败（{str(e)}），降级到单行匹配...")
                return [self.__process_single(all_subs, item, stats) for item in batch]
            split_at = max(1, len(batch) // 2)
            logger.warning(
                f"批次翻译失败（{str(e)}），拆分为 {split_at}/{len(batch) - split_at} 行重试..."
            )
            return (
                self.__process_batch(all_subs, batch[:split_at], stats)
                + self.__process_batch(all_subs, batch[split_at:], stats)
            )

    def __process_single(self, all_subs: List[srt.Subtitle], item: srt.Subtitle, stats: dict) -> srt.Subtitle:
        """单条处理逻辑"""
        idx = all_subs.index(item)
        context = self.__get_context(all_subs, [idx], is_batch=False) if self._context_window > 0 else None
        success, trans = self.__translate_to_zh(item.content, context)

        if success:
            item.content = f"{trans}\n{item.content}"
            stats['line_fallback'] += 1
            return item
        else:
            item.content = f"[翻译失败]\n{item.content}"
            stats['line_fail'] += 1
            return item

    def __translate_zh_subtitle(self, source_lang: str, source_subtitle: str, dest_subtitle: str,
                                task: Optional[TaskItem] = None):
        if self.__is_chinese_language(source_lang):
            logger.info(f"源字幕语言已是中文（{source_lang}），跳过翻译，不生成机翻字幕：{dest_subtitle}")
            self.__update_task_progress(task, 98, "同语言跳过", f"源字幕语言已是中文：{source_lang}", force=True)
            return True

        stats = {'total': 0, 'batch_success': 0, 'batch_fail': 0, 'line_fallback': 0, 'line_fail': 0}
        subs = self.__load_srt(source_subtitle)
        if source_lang in ["en", "eng"] and self._enable_merge:
            valid_subs = self.__merge_srt(subs)
            logger.info(f"英文字幕合并：合并前字幕数: {len(subs)},合并后字幕数: {len(valid_subs)}")
        else:
            valid_subs = subs

        if not valid_subs:
            logger.warning("字幕文件为空或没有有效的字幕条目，跳过翻译")
            # 创建一个空的字幕文件
            self.__save_srt(dest_subtitle, [])
            self.__update_task_progress(task, 98, "翻译完成", "字幕为空，已生成空中文字幕", force=True)
            return True

        stats['total'] = len(valid_subs)
        self.__update_task_progress(task, 76, "翻译字幕", f"共 {len(valid_subs)} 行", force=True)
        checkpoint = self.__get_translate_checkpoint(task, source_subtitle, dest_subtitle, len(valid_subs))
        checkpoint_items = (checkpoint or {}).get("items") or {}
        cached_count = 0
        while cached_count < len(valid_subs) and str(cached_count) in checkpoint_items:
            valid_subs[cached_count].content = checkpoint_items[str(cached_count)]
            cached_count += 1
        processed = list(valid_subs[:cached_count])
        if cached_count:
            cached_fail = sum(1 for item in processed if (item.content or "").startswith("[翻译失败]"))
            stats['line_fail'] += cached_fail
            stats['batch_success'] += cached_count - cached_fail
            percent = self.__scale_progress(76, 98, len(processed), len(valid_subs))
            self.__update_task_progress(task, percent, "翻译字幕",
                                        f"断点恢复 {len(processed)}/{len(valid_subs)} 行", force=True)
        current_batch = []

        for item in valid_subs[cached_count:]:
            current_batch.append(item)

            if len(current_batch) >= self._batch_size:
                processed += self.__process_items(valid_subs, current_batch, stats)
                current_batch = []
                self.__save_translate_checkpoint(task, source_subtitle, dest_subtitle, len(valid_subs), processed)
                logger.info(f"进度: {len(processed)}/{len(valid_subs)}")
                percent = self.__scale_progress(76, 98, len(processed), len(valid_subs))
                self.__update_task_progress(task, percent, "翻译字幕",
                                            f"{len(processed)}/{len(valid_subs)} 行")

        if current_batch:
            processed += self.__process_items(valid_subs, current_batch, stats)
            self.__save_translate_checkpoint(task, source_subtitle, dest_subtitle, len(valid_subs), processed)
            percent = self.__scale_progress(76, 98, len(processed), len(valid_subs))
            self.__update_task_progress(task, percent, "翻译字幕",
                                        f"{len(processed)}/{len(valid_subs)} 行", force=True)

        translated_count = stats['batch_success'] + stats['line_fallback']
        if stats['total'] > 0 and translated_count <= 0:
            logger.error("字幕翻译全部失败，不生成中文字幕文件")
            self.__update_task_progress(task, task.progress if task else 76, "翻译失败",
                                        f"全部 {stats['total']} 行翻译失败", force=True)
            self.__clear_translate_checkpoint(task, source_subtitle, dest_subtitle)
            return False

        self.__save_srt(dest_subtitle, processed)
        self.__clear_translate_checkpoint(task, source_subtitle, dest_subtitle)
        detail = f"已翻译 {translated_count}/{stats['total']} 行"
        if stats['line_fail']:
            detail += f"，失败 {stats['line_fail']} 行"
        self.__update_task_progress(task, 98, "翻译完成", detail, force=True)

        success_rate = (translated_count / stats['total'] * 100) if stats['total'] > 0 else 0.0

        logger.info(f"""
    翻译完成！
    总处理条目: {stats['total']}
    批次成功: {stats['batch_success']} ({success_rate:.1f}%)
    批次失败: {stats['batch_fail']}
    行补偿翻译: {stats['line_fallback']}
    行翻译失败: {stats['line_fail']}
            """)
        return True

    @staticmethod
    def __is_failed_machine_subtitle(subtitle_path: str) -> bool:
        try:
            subs = AutoSubRemoteAsr.__load_srt(subtitle_path)
        except Exception:
            return False
        if not subs:
            return False
        failed = sum(1 for item in subs if "[翻译失败]" in (item.content or ""))
        return failed == len(subs) or failed / len(subs) >= 0.8

    @staticmethod
    def __external_subtitle_exists(video_file, prefer_langs=None, only_srt=False, strict=True):
        """
        外部字幕文件是否存在,支持多种格式及扩展需求。
        :param video_file: 视频文件路径
        :param prefer_langs: 偏好语言列表，支持单个语言字符串或列表
        :param only_srt: 是否只匹配srt格式的字幕
        :param strict: 是否严格匹配偏好语言.当不存在偏好语言字幕但存在其他语言字幕时,是否返回其他字幕
        :return: 元组 (是否存在, 检测到的语言, 文件名)
        """
        video_dir, video_name = os.path.split(video_file)
        video_name, video_ext = os.path.splitext(video_name)

        if prefer_langs and type(prefer_langs) == str:
            prefer_langs = [prefer_langs]

        metadata_flags = ["default", "forced", "foreign", "sdh", "cc", "hi", "机翻"]
        if only_srt:
            subtitle_extensions = [".srt"]
        else:
            subtitle_extensions = [".srt", ".sub", ".ass", ".ssa", ".vtt"]

        def parse_props(props):
            """
            解析字幕属性信息，提取语言和元数据标记。
            :param props: 属性字符串
            :return: (语言, 元数据列表)
            """
            parts = props.split(".")
            if len(parts) < 1:
                return None, []

            cur_subtitle_lang = None
            cur_metadata = []
            # 倒序遍历文件名中的标记
            for i in range(len(parts) - 1, -1, -1):
                part = parts[i]
                if part in metadata_flags:
                    cur_metadata.append(part)
                elif cur_subtitle_lang is None:
                    try:
                        iso639.to_iso639_1(part)
                    except iso639.NonExistentLanguageError:
                        continue
                    else:
                        cur_subtitle_lang = iso639.to_iso639_1(part)  # 记录最后一个语言标记

            return cur_subtitle_lang, cur_metadata

        # 备选的字幕语言.当strict=False时生效, 用于在未找到偏好语言时返回其他语言
        second_lang = None
        second_file = None
        # 检查字幕文件
        for file in os.listdir(video_dir):
            if not file.startswith(video_name):
                continue

            # 检查扩展名是否在支持范围内
            _, ext = os.path.splitext(file)
            if ext.lower() not in subtitle_extensions:
                continue

            # 提取文件名中的语言和元数据信息
            props_str = file[len(video_name) + 1: -len(ext)] if file.startswith(video_name + ".") else ""
            subtitle_lang, metadata = parse_props(props_str)

            # 如果没有语言标记，跳过
            if not subtitle_lang:
                continue
            if "机翻" in metadata and AutoSubRemoteAsr.__is_failed_machine_subtitle(os.path.join(video_dir, file)):
                logger.warn(f"跳过无效机翻字幕：{file}")
                continue

            # 如果指定了偏好语言
            if prefer_langs:
                if subtitle_lang in prefer_langs:
                    return True, subtitle_lang, file
                else:
                    second_lang = subtitle_lang
                    second_file = file
            else:
                # 未指定偏好语言，找到的第一个字幕即返回
                return True, subtitle_lang, file
        if not strict and second_lang:
            return True, second_lang, second_file
        return False, None, None

    def __target_subtitle_reason(self, video_file) -> str:
        """
        目标软字幕文件是否存在
        :param video_file:
        :return:
        """
        if self._translate_zh:
            prefer_langs = ['zh', 'chi', 'zh-CN', 'chs', 'zhs', 'zh-Hans', 'zhong', 'simp', 'cn']
            strict = True
        else:
            if self._translate_preference == "english_first":
                prefer_langs = ['en', 'eng']
                strict = False
            elif self._translate_preference == "english_only":
                prefer_langs = ['en', 'eng']
                strict = True
            else:
                prefer_langs = None
                strict = False

        exist, lang, name = self.__external_subtitle_exists(video_file, prefer_langs, strict=strict)
        if exist:
            return f"外挂字幕已存在：{name or '-'}（{lang or '未知'}）"

        video_meta = Ffmpeg().get_video_metadata(video_file)
        if not video_meta:
            return ""
        ret, subtitle_index, subtitle_lang = self.__get_video_prefer_subtitle(video_meta, prefer_lang=prefer_langs,
                                                                              only_srt=False)
        if ret and (not prefer_langs or subtitle_lang in prefer_langs):
            return f"内嵌字幕已存在：轨道 {subtitle_index}（{subtitle_lang or '未知'}）"

        return ""

    def __target_subtitle_exists(self, video_file):
        """
        目标软字幕文件是否存在
        :param video_file:
        :return:
        """
        return bool(self.__target_subtitle_reason(video_file))

    @staticmethod
    def __hard_subtitle_sample_times(duration: float) -> List[float]:
        if duration <= 0:
            return []
        margin = min(max(duration * 0.08, 20), max(duration * 0.20, 5))
        start = min(max(3, margin), max(duration - 3, 3))
        end = max(start + 1, duration - margin)
        points = [0.06, 0.16, 0.27, 0.38, 0.50, 0.62, 0.73, 0.84, 0.94]
        samples = []
        for point in points:
            pos = start + (end - start) * point
            pos = min(max(1, pos), max(1, duration - 1))
            if not samples or abs(pos - samples[-1]) >= 3:
                samples.append(pos)
        return samples

    @staticmethod
    def __edge_band_features(frame: bytes, width: int, height: int,
                             y0: int, y1: int, x0: int, x1: int) -> Dict[str, Any]:
        y0 = max(0, min(height - 2, y0))
        y1 = max(y0 + 2, min(height - 1, y1))
        x0 = max(0, min(width - 2, x0))
        x1 = max(x0 + 2, min(width - 1, x1))
        band_w = x1 - x0
        band_h = y1 - y0
        total = max(1, band_w * band_h)
        mask = bytearray(total)
        row_counts = [0] * band_h
        col_counts = [0] * band_w
        edge_count = 0
        bright_count = 0
        dark_count = 0

        for yy in range(y0, y1):
            row_offset = yy * width
            next_row_offset = min(height - 1, yy + 1) * width
            band_y = yy - y0
            for xx in range(x0, x1):
                idx = row_offset + xx
                value = frame[idx]
                if value >= 190:
                    bright_count += 1
                elif value <= 55:
                    dark_count += 1
                if yy >= height - 1 or xx >= width - 1:
                    continue
                gradient = abs(value - frame[idx + 1]) + abs(value - frame[next_row_offset + xx])
                if gradient < 70:
                    continue
                band_x = xx - x0
                mask_index = band_y * band_w + band_x
                mask[mask_index] = 1
                row_counts[band_y] += 1
                col_counts[band_x] += 1
                edge_count += 1

        row_threshold = max(3, int(band_w * 0.018))
        col_threshold = max(1, int(band_h * 0.012))
        active_rows = [idx for idx, count in enumerate(row_counts) if count >= row_threshold]
        active_cols = [idx for idx, count in enumerate(col_counts) if count >= col_threshold]

        visited = bytearray(total)
        components = 0
        eligible_components = 0
        max_component_area = 0
        for start_index, value in enumerate(mask):
            if not value or visited[start_index]:
                continue
            stack = [start_index]
            visited[start_index] = 1
            area = 0
            min_x = band_w
            max_x = 0
            min_y = band_h
            max_y = 0
            while stack:
                current = stack.pop()
                cy, cx = divmod(current, band_w)
                area += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                if cx > 0:
                    nxt = current - 1
                    if mask[nxt] and not visited[nxt]:
                        visited[nxt] = 1
                        stack.append(nxt)
                if cx < band_w - 1:
                    nxt = current + 1
                    if mask[nxt] and not visited[nxt]:
                        visited[nxt] = 1
                        stack.append(nxt)
                if cy > 0:
                    nxt = current - band_w
                    if mask[nxt] and not visited[nxt]:
                        visited[nxt] = 1
                        stack.append(nxt)
                if cy < band_h - 1:
                    nxt = current + band_w
                    if mask[nxt] and not visited[nxt]:
                        visited[nxt] = 1
                        stack.append(nxt)

            if area < 3:
                continue
            components += 1
            max_component_area = max(max_component_area, area)
            box_w = max_x - min_x + 1
            box_h = max_y - min_y + 1
            if 2 <= box_w <= int(band_w * 0.85) and 3 <= box_h <= 38 and 4 <= area <= 900:
                eligible_components += 1

        row_span = (active_rows[-1] - active_rows[0] + 1) if active_rows else 0
        col_span = (active_cols[-1] - active_cols[0] + 1) if active_cols else 0
        return {
            "edge_density": edge_count / total,
            "bright_density": bright_count / total,
            "dark_density": dark_count / total,
            "active_rows": len(active_rows),
            "row_span": row_span,
            "col_coverage": col_span / max(1, band_w),
            "components": components,
            "eligible_components": eligible_components,
            "max_component_area": max_component_area,
        }

    @classmethod
    def __analyze_hard_subtitle_frame(cls, frame: bytes, width: int = 320, height: int = 180) -> Dict[str, Any]:
        if not frame or len(frame) < width * height:
            return {"positive": False, "score": 0.0, "reason": "empty"}

        x0 = int(width * 0.08)
        x1 = int(width * 0.92)
        subtitle_band = cls.__edge_band_features(
            frame, width, height,
            int(height * 0.58), int(height * 0.94), x0, x1
        )
        ref_band = cls.__edge_band_features(
            frame, width, height,
            int(height * 0.24), int(height * 0.52), x0, x1
        )

        subtitle_density = subtitle_band["edge_density"]
        ref_density = ref_band["edge_density"]
        ratio = subtitle_density / max(ref_density, 0.004)
        delta = subtitle_density - ref_density
        density_score = min(1.0, max(0.0, (subtitle_density - 0.010) / 0.055))
        component_score = min(1.0, subtitle_band["eligible_components"] / 16)
        coverage_score = min(1.0, subtitle_band["col_coverage"] / 0.34)
        row_span = subtitle_band["row_span"]
        if 8 <= row_span <= 46:
            row_score = 1.0
        elif 5 <= row_span <= 58:
            row_score = 0.55
        else:
            row_score = 0.15
        contrast_score = min(
            1.0,
            max(subtitle_band["bright_density"], subtitle_band["dark_density"]) / 0.055
        )
        score = (
            density_score * 0.30
            + component_score * 0.26
            + coverage_score * 0.20
            + row_score * 0.14
            + contrast_score * 0.10
        )
        if ratio < 1.08 and delta < 0.008:
            score *= 0.72
        if subtitle_density > 0.20 or subtitle_band["active_rows"] > 58:
            score *= 0.70
        if subtitle_band["max_component_area"] > 1800:
            score *= 0.82

        positive = (
            score >= 0.58
            and subtitle_band["eligible_components"] >= 6
            and subtitle_band["col_coverage"] >= 0.16
            and 0.012 <= subtitle_density <= 0.22
            and (ratio >= 1.08 or delta >= 0.008)
        )
        return {
            "positive": positive,
            "score": round(score, 3),
            "ratio": round(ratio, 2),
            "delta": round(delta, 4),
            "density": round(subtitle_density, 4),
            "ref_density": round(ref_density, 4),
            "components": subtitle_band["eligible_components"],
            "coverage": round(subtitle_band["col_coverage"], 3),
            "row_span": subtitle_band["row_span"],
        }

    def __hard_subtitle_reason(self, video_file: str, task: Optional[TaskItem] = None) -> str:
        video_meta = Ffmpeg().get_video_metadata(video_file, stop_event=self._event)
        if self._event.is_set():
            raise UserInterruptException("用户中断当前任务")
        duration = self.__get_video_duration(video_meta)
        samples = self.__hard_subtitle_sample_times(duration)
        if not samples:
            logger.info(f"硬字幕检测跳过：未读取到有效时长 {video_file}")
            return ""

        frame_width = 320
        frame_height = 180
        self.__update_task_progress(task, 25, "检测硬字幕", f"抽样 {len(samples)} 帧", force=True)
        results = []
        failed = 0
        for index, start_second in enumerate(samples, 1):
            if self._event.is_set():
                raise UserInterruptException("用户中断当前任务")
            ok, frame, error = Ffmpeg().read_video_gray_frame(
                video_file,
                start_seconds=start_second,
                width=frame_width,
                height=frame_height,
                stop_event=self._event,
                threads=self._cpu_threads,
                timeout=25,
            )
            if self._event.is_set():
                raise UserInterruptException("用户中断当前任务")
            if not ok:
                failed += 1
                logger.warn(f"硬字幕检测抽帧失败：{video_file} {start_second:.1f}s - {error}")
                continue
            result = self.__analyze_hard_subtitle_frame(frame, frame_width, frame_height)
            result["time"] = round(start_second, 1)
            results.append(result)
            progress = self.__scale_progress(25, 29, index, len(samples))
            positives = sum(1 for item in results if item.get("positive"))
            self.__update_task_progress(
                task,
                progress,
                "检测硬字幕",
                f"抽样 {index}/{len(samples)}，疑似 {positives}",
            )

        if len(results) < max(3, min(5, len(samples) - failed)):
            logger.info(f"硬字幕检测样本不足：{video_file} success={len(results)} failed={failed}")
            return ""

        positives = sum(1 for item in results if item.get("positive"))
        strong = sum(1 for item in results if float(item.get("score") or 0) >= 0.74)
        avg_score = sum(float(item.get("score") or 0) for item in results) / len(results)
        required = 3 if len(results) < 6 else max(4, math.ceil(len(results) * 0.55))
        detected = positives >= required and avg_score >= 0.48 and (strong >= 1 or positives >= required + 1)
        logger.info(
            f"硬字幕检测结果：detected={detected} samples={len(results)} failed={failed} "
            f"positive={positives} strong={strong} avg={avg_score:.2f} details={_log_preview(results, 1600)}"
        )
        if not detected:
            return ""
        confidence = min(0.99, max(avg_score, positives / max(1, len(results))))
        return f"检测到硬字幕（{positives}/{len(results)} 帧，置信度 {confidence:.2f}）"

    @staticmethod
    def __form_col(content: Any, md: int = 4, cols: int = 12, props: Optional[dict] = None) -> dict:
        col_props = {"cols": cols}
        if md:
            col_props["md"] = md
        if props:
            col_props.update(props)
        return {
            "component": "VCol",
            "props": col_props,
            "content": content if isinstance(content, list) else [content],
        }

    @staticmethod
    def __form_switch(model: str, label: str, hint: str = None, color: str = None, props: Optional[dict] = None) -> dict:
        switch_props = {"model": model, "label": label}
        if hint:
            switch_props["hint"] = hint
        if color:
            switch_props["color"] = color
        if props:
            switch_props.update(props)
        return {"component": "VSwitch", "props": switch_props}

    @staticmethod
    def __form_text(model: str, label: str, placeholder: str = None, hint: str = None,
                    props: Optional[dict] = None) -> dict:
        text_props = {"model": model, "label": label}
        if placeholder is not None:
            text_props["placeholder"] = placeholder
        if hint:
            text_props["hint"] = hint
        if props:
            text_props.update(props)
        return {"component": "VTextField", "props": text_props}

    @staticmethod
    def __form_textarea(model: str, label: str, placeholder: str = None, hint: str = None,
                        rows: int = 3) -> dict:
        props = {"model": model, "label": label, "rows": rows}
        if placeholder is not None:
            props["placeholder"] = placeholder
        if hint:
            props["hint"] = hint
        return {"component": "VTextarea", "props": props}

    @staticmethod
    def __form_select(model: str, label: str, items: List[dict], hint: str = None) -> dict:
        props = {"model": model, "label": label, "items": items}
        if hint:
            props["hint"] = hint
        return {"component": "VSelect", "props": props}

    @staticmethod
    def __form_card(title: str, subtitle: str, content: List[dict]) -> dict:
        card_content = [{"component": "VCardTitle", "text": title}]
        if subtitle:
            card_content.append({"component": "VCardSubtitle", "text": subtitle})
        card_content.append({"component": "VCardText", "content": content})
        return {
            "component": "VCard",
            "props": {"variant": "tonal", "class": "autosub-asr-form-card mb-3"},
            "content": card_content,
        }

    @staticmethod
    def __responsive_style() -> dict:
        return {
            "component": "style",
            "text": """
            .autosub-asr-page,
            .autosub-asr-form {
                min-width: 0;
            }
            .autosub-asr-actions-col {
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
            }
            .autosub-asr-actions-col .v-btn {
                margin: 0 !important;
            }
            .autosub-asr-task-scroll {
                max-height: 64vh;
                overflow-y: auto;
                overflow-x: auto;
            }
            .autosub-asr-progress {
                gap: 8px;
                min-width: 0;
                width: 100%;
            }
            .autosub-asr-progress-detail {
                max-width: 420px;
                white-space: normal;
                word-break: break-word;
            }
            .autosub-asr-table td {
                vertical-align: top;
            }
            @media (max-width: 700px) {
                .autosub-asr-form .v-card-title {
                    font-size: 1rem !important;
                    line-height: 1.25 !important;
                    white-space: normal !important;
                    padding: 12px 12px 4px !important;
                }
                .autosub-asr-form .v-card-subtitle {
                    line-height: 1.35 !important;
                    white-space: normal !important;
                    padding: 0 12px !important;
                }
                .autosub-asr-form .v-card-text {
                    padding: 10px 12px 12px !important;
                }
                .autosub-asr-form .v-row,
                .autosub-asr-page .v-row {
                    margin: -4px !important;
                }
                .autosub-asr-form .v-col,
                .autosub-asr-page .v-col {
                    padding: 4px !important;
                }
                .autosub-asr-form .v-switch .v-label {
                    font-size: .9rem !important;
                    line-height: 1.25 !important;
                    white-space: normal !important;
                }
                .autosub-asr-form .v-input,
                .autosub-asr-form .v-field {
                    min-width: 0 !important;
                }
                .autosub-asr-form .v-input__details {
                    min-height: 0 !important;
                    padding-top: 2px !important;
                }
                .autosub-asr-form textarea {
                    min-height: 88px !important;
                }
                .autosub-asr-actions-col {
                    display: grid !important;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 8px;
                }
                .autosub-asr-actions-col .v-btn {
                    width: 100%;
                    min-width: 0 !important;
                    padding-inline: 8px !important;
                }
                .autosub-asr-actions-col .v-btn__content {
                    min-width: 0;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                }
                .autosub-asr-metric {
                    padding: 10px !important;
                }
                .autosub-asr-task-shell {
                    border-radius: 8px !important;
                }
                .autosub-asr-tabs {
                    max-height: 104px !important;
                    overflow-y: auto !important;
                    overflow-x: hidden !important;
                }
                .autosub-asr-tabs .v-slide-group__content {
                    flex-wrap: wrap !important;
                    align-content: flex-start;
                }
                .autosub-asr-tabs .v-tab {
                    flex: 1 0 50%;
                    min-width: 0 !important;
                    height: 42px !important;
                    font-size: .82rem !important;
                }
                .autosub-asr-task-scroll {
                    max-height: 60vh !important;
                    overflow-x: hidden !important;
                }
                .autosub-asr-table table,
                .autosub-asr-table thead,
                .autosub-asr-table tbody,
                .autosub-asr-table tr,
                .autosub-asr-table td {
                    display: block;
                    width: 100%;
                }
                .autosub-asr-table thead {
                    display: none;
                }
                .autosub-asr-table tr {
                    margin: 10px 8px;
                    padding: 10px;
                    border: 1px solid rgba(128, 128, 128, .24);
                    border-radius: 8px;
                }
                .autosub-asr-table td {
                    border-bottom: 0 !important;
                    padding: 4px 0 !important;
                }
                .autosub-asr-table td:nth-child(2)::before,
                .autosub-asr-table td:nth-child(3)::before,
                .autosub-asr-table td:nth-child(4)::before,
                .autosub-asr-table td:nth-child(5)::before {
                    display: block;
                    margin-bottom: 2px;
                    color: rgba(128, 128, 128, .92);
                    font-size: .72rem;
                    line-height: 1.2;
                }
                .autosub-asr-table td:nth-child(2)::before {
                    content: "来源";
                }
                .autosub-asr-table td:nth-child(3)::before {
                    content: "状态";
                }
                .autosub-asr-table td:nth-child(4)::before {
                    content: "进度";
                }
                .autosub-asr-table td:nth-child(5)::before {
                    content: "时间";
                }
                .autosub-asr-progress .v-progress-linear {
                    min-width: 96px !important;
                    max-width: none !important;
                    flex: 1 1 auto;
                }
                .autosub-asr-progress-detail {
                    max-width: none !important;
                }
            }
            @media (max-width: 380px) {
                .autosub-asr-actions-col {
                    grid-template-columns: 1fr;
                }
            }
            """
        }

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        base_settings = self.__form_card("基础开关", "插件是否运行，以及哪些入口可以创建任务", [
            {
                "component": "VRow",
                "content": [
                    self.__form_col(self.__form_switch("enabled", "启用插件", color="primary"), md=3),
                    self.__form_col(self.__form_switch("auto_scan_enabled", "定时扫描媒体路径"), md=3),
                    self.__form_col(self.__form_switch("listen_transfer_event", "媒体入库自动执行"), md=3),
                    self.__form_col(self.__form_switch("send_notify", "发送通知"), md=3),
                ],
            },
        ])

        scan_settings = self.__form_card("扫描与队列", "控制扫描范围、定时周期和后台并行", [
            {
                "component": "VRow",
                "content": [
                    self.__form_col(
                        self.__form_textarea(
                            "path_list",
                            "媒体路径",
                            "绝对路径，每行一个。支持文件和文件夹",
                            "手动扫描和定时扫描共用这些路径",
                            rows=4,
                        ),
                        md=6,
                    ),
                    self.__form_col(
                        self.__form_textarea(
                            "exclude_path_list",
                            "排除路径",
                            "每行一个，可填绝对路径或关键词",
                            "命中后定时扫描、手动扫描和入库触发都会跳过",
                            rows=4,
                        ),
                        md=6,
                    ),
                ],
            },
            {
                "component": "VRow",
                "content": [
                    self.__form_col(
                        self.__form_text(
                            "auto_scan_cron",
                            "扫描周期",
                            "*/10 * * * *",
                            "默认每10分钟扫描一次",
                        ),
                        md=3,
                    ),
                    self.__form_col(
                        self.__form_text(
                            "parallel_tasks",
                            "并行任务数",
                            "1",
                            "同一时间处理的视频数量",
                        ),
                        md=3,
                    ),
                    self.__form_col(
                        self.__form_text(
                            "integrity_retry_minutes",
                            "不完整重试分钟",
                            "10",
                            "文件未完整时等待后重新检查",
                        ),
                        md=3,
                    ),
                    self.__form_col(
                        self.__form_switch(
                            "full_integrity_check",
                            "完整解码校验",
                            "高CPU，仅排查视频损坏时开启",
                        ),
                        md=3,
                    ),
                ],
            },
        ])

        subtitle_settings = self.__form_card("字幕策略", "控制字幕来源、语言判断和 ASR 分段", [
            {
                "component": "VRow",
                "content": [
                    self.__form_col(self.__form_switch("enable_asr", "无字幕时调用ASR"), md=3),
                    self.__form_col(self.__form_switch("translate_zh", "翻译成中文"), md=3),
                    self.__form_col(self.__form_switch("auto_detect_language", "ASR自动检测语言"), md=3),
                    self.__form_col(
                        self.__form_select(
                            "translate_preference",
                            "字幕源偏好",
                            [
                                {"title": "英文优先", "value": "english_first"},
                                {"title": "仅英文", "value": "english_only"},
                                {"title": "原音优先", "value": "origin_first"},
                            ],
                        ),
                        md=3,
                    ),
                ],
            },
            {
                "component": "VRow",
                "content": [
                    self.__form_col(
                        self.__form_text(
                            "asr_api_model",
                            "ASR模型",
                            "whisper-1",
                            "需要支持 verbose_json 和 segments",
                        ),
                        md=3,
                    ),
                    self.__form_col(
                        self.__form_text(
                            "asr_chunk_minutes",
                            "音频分段分钟",
                            "10",
                            "范围5-30；过短会增加接口调用",
                        ),
                        md=3,
                    ),
                    self.__form_col(
                        self.__form_text(
                            "asr_request_timeout",
                            "ASR超时秒",
                            "300",
                            "单段ASR请求超时后重试",
                        ),
                        md=3,
                    ),
                    self.__form_col(
                        self.__form_text("max_retries", "接口重试次数", "3"),
                        md=3,
                    ),
                ],
            },
            {
                "component": "VRow",
                "content": [
                    self.__form_col(
                        self.__form_textarea(
                            "asr_prompt",
                            "ASR提示词",
                            self.__default_asr_prompt(),
                            "传给音频转写接口；用于固定原文转写风格，留空则不发送",
                            rows=2,
                        ),
                        md=12,
                    ),
                ],
            },
        ])

        api_settings = self.__form_card("接口配置", "当前插件独立维护接口地址、密钥和模型", [
            {
                "component": "VRow",
                "content": [
                    self.__form_col(
                        self.__form_text(
                            "openai_url",
                            "接口地址",
                            "https://api.openai.com",
                            "填写 OpenAI 兼容接口根地址；带不带 /v1 都可以",
                        ),
                        md=4,
                    ),
                    self.__form_col(self.__form_text("openai_key", "API密钥", "sk-xxx"), md=4),
                    self.__form_col(
                        self.__form_text(
                            "openai_model",
                            "翻译模型",
                            "gpt-5-chat-latest",
                            "只用于中文字幕翻译，不影响ASR模型",
                        ),
                        md=4,
                    ),
                ],
            },
            {
                "component": "VRow",
                "content": [
                    self.__form_col(
                        self.__form_text(
                            "translate_request_timeout",
                            "翻译超时秒",
                            "120",
                            "单次翻译请求超时后重试",
                        ),
                        md=4,
                    ),
                    self.__form_col(
                        [
                            self.__form_switch("openai_proxy", "使用代理服务器"),
                            self.__form_switch(
                                "detailed_log",
                                "详细接口日志",
                                "记录完整接口入参和返回，排查问题时再开启",
                            ),
                        ],
                        md=8,
                    ),
                ],
            },
        ])

        translate_settings = self.__form_card("翻译参数", "控制字幕翻译批次、上下文和并发", [
            {
                "component": "VRow",
                "content": [
                    self.__form_col(
                        self.__form_text(
                            "translate_concurrency",
                            "翻译接口并发数",
                            "1",
                            "同一时间发起的翻译请求数量",
                        ),
                        md=3,
                    ),
                    self.__form_col(
                        self.__form_text(
                            "batch_size",
                            "每批翻译行数",
                            "20",
                            "建议20-25；接口稳定时再提高",
                        ),
                        md=3,
                    ),
                    self.__form_col(self.__form_text("context_window", "上下文窗口", "5"), md=3),
                    self.__form_col(
                        self.__form_switch(
                            "enable_merge",
                            "英文字幕合并整句",
                            "仅英文字幕需要时开启",
                        ),
                        md=3,
                    ),
                ],
            },
        ])

        return [
            self.__responsive_style(),
            {
                "component": "VForm",
                "props": {"class": "autosub-asr-form"},
                "content": [
                    base_settings,
                    scan_settings,
                    subtitle_settings,
                    api_settings,
                    translate_settings,
                ],
            }
        ], {
            "enabled": False,
            "reset_tasks": False,
            "clear_history": False,
            "send_notify": False,
            "detailed_log": False,
            "retry_failed_once": False,
            "listen_transfer_event": True,
            "run_now": False,
            "path_list": "",
            "exclude_path_list": "",
            "auto_scan_enabled": True,
            "auto_scan_cron": "*/10 * * * *",
            "integrity_retry_minutes": 10,
            "parallel_tasks": 1,
            "translate_concurrency": 1,
            "full_integrity_check": False,
            "translate_preference": "english_first",
            "translate_zh": True,
            "enable_asr": True,
            "auto_detect_language": True,
            "asr_api_model": "whisper-1",
            "asr_chunk_minutes": 10,
            "asr_request_timeout": 300,
            "asr_prompt": self.__default_asr_prompt(),
            "translate_request_timeout": 120,
            "openai_proxy": False,
            "openai_url": "https://api.openai.com",
            "openai_key": None,
            "openai_model": "gpt-5-chat-latest",
            "context_window": 5,
            "max_retries": 3,
            "enable_merge": False,
            "enable_batch": True,
            "batch_size": 20,
        }

    def __legacy_get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                            'color': 'primary'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'reset_tasks',
                                            'label': '重置任务记录',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'send_notify',
                                            'label': '发送通知'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'retry_failed_once',
                                            'label': '失败任务重试一次',
                                            'color': 'secondary',
                                            'hint': '将失败状态任务重新加入队列，执行后自动关闭'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'full_integrity_check',
                                            'label': '完整解码校验',
                                            'hint': '高CPU。关闭时仅用ffprobe元数据判断视频是否可处理'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'listen_transfer_event',
                                            'label': '媒体入库自动执行',
                                            'hint': '监听媒体入库事件，自动执行字幕生成'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'run_now',
                                            'label': '手动执行一次',
                                            'color': 'secondary'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'auto_scan_enabled',
                                            'label': '定时扫描媒体路径',
                                            'hint': '按周期扫描媒体路径，新完整视频自动加入队列'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {'v-show': 'run_now || auto_scan_enabled'},
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 8},
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'path_list',
                                            'label': '媒体路径',
                                            'rows': 3,
                                            'placeholder': '绝对路径，每行一个。支持文件和文件夹',
                                            'hint': '手动执行和定时扫描共用'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 2, 'v-show': 'auto_scan_enabled'},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'auto_scan_cron',
                                            'label': '扫描周期(cron)',
                                            'placeholder': '*/10 * * * *'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 2},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'integrity_retry_minutes',
                                            'label': '不完整重试(分钟)',
                                            'placeholder': '30',
                                            'hint': '不完整视频不计失败，到期后重新检查'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'translate_preference',
                                            'label': '字幕源语言偏好',
                                            'hint': '小语种视频存在多语言字幕/音轨时，优先选择哪种语言用于翻译',
                                            'items': [
                                                {'title': '仅英文', 'value': 'english_only'},
                                                {'title': '英文优先', 'value': 'english_first'},
                                                {'title': '原音优先', 'value': 'origin_first'}
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'parallel_tasks',
                                            'label': '并行任务数',
                                            'placeholder': '默认1，建议1-2',
                                            'hint': '同一时间处理的字幕任务数量'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'translate_zh',
                                            'label': '翻译成中文',
                                            'hint': '使用大模型翻译成中文字幕'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable_asr',
                                            'label': '允许从音轨生成字幕'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4, 'v-show': 'enable_asr'},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'auto_detect_language',
                                            'label': '自动检测语言',
                                            'hint': '由接口ASR自动检测语言，而非依赖视频元数据'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4, 'v-show': 'enable_asr'},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'reuse_autosub_config',
                                            'label': '复用私有版接口配置',
                                            'hint': '复用 AI字幕自动生成(v2) 私有版 的接口地址、密钥和翻译模型'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {'v-show': 'enable_asr'},
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'asr_api_model',
                                            'label': 'ASR接口模型',
                                            'placeholder': 'whisper-1',
                                            'hint': '需要支持 verbose_json 和 segments，默认 whisper-1'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'asr_chunk_minutes',
                                            'label': '音频分段分钟',
                                            'placeholder': '10',
                                            'hint': '范围5-30，建议10-15；过短会增加接口调用次数'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {'v-show': 'enable_asr'},
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'asr_prompt',
                                            'label': 'ASR提示词',
                                            'placeholder': self.__default_asr_prompt(),
                                            'hint': '传给音频转写接口；用于固定原文转写风格，留空则不发送',
                                            'rows': 2
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VExpansionPanels',
                        'props': {'variant': 'accordion', 'multiple': True},
                        'content': [
                            {
                                'component': 'VExpansionPanel',
                                'props': {'v-show': 'enable_asr || translate_zh'},
                                'content': [
                                    {
                                        'component': 'VExpansionPanelTitle',
                                        'text': '大模型接口设置'
                                    },
                                    {
                                        'component': 'VExpansionPanelText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 3},
                                                        'content': [
                                                            {
                                                                'component': 'VSwitch',
                                                                'props': {
                                                                    'model': 'reuse_autosub_config',
                                                                    'label': '复用私有版接口配置',
                                                                    'hint': '关闭后使用当前插件填写的接口地址、密钥和翻译模型'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 12,
                                                            'md': 3,
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VSwitch',
                                                                'props': {
                                                                    'model': 'detailed_log',
                                                                    'label': '详细接口日志',
                                                                    'hint': '开启后记录完整接口入参和返回，日志量很大'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 12,
                                                            'md': 3,
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VSwitch',
                                                                'props': {
                                                                    'model': 'openai_proxy',
                                                                    'label': '使用代理服务器',
                                                                    'v-show': '!reuse_autosub_config',
                                                                    'v-if': '!reuse_autosub_config'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 12,
                                                            'md': 3,
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VSwitch',
                                                                'props': {
                                                                    'model': 'compatible',
                                                                    'label': '兼容模式',
                                                                    'v-show': '!reuse_autosub_config'
                                                                }
                                                            }
                                                        ]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 12,
                                                            'md': 4
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'openai_url',
                                                                    'label': 'OpenAI API Url',
                                                                    'placeholder': 'https://api.openai.com',
                                                                    'v-show': '!reuse_autosub_config'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 12,
                                                            'md': 4
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'openai_key',
                                                                    'label': 'API密钥',
                                                                    'placeholder': 'sk-xxx',
                                                                    'v-show': '!reuse_autosub_config'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 12,
                                                            'md': 4
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'openai_model',
                                                                    'label': '翻译模型',
                                                                    'placeholder': 'gpt-5-chat-latest',
                                                                    'v-show': '!reuse_autosub_config'
                                                                }
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            },
                            {
                                'component': 'VExpansionPanel',
                                'props': {'v-show': 'translate_zh'},
                                'content': [
                                    {
                                        'component': 'VExpansionPanelTitle',
                                        'text': '翻译参数设置'
                                    },
                                    {
                                        'component': 'VExpansionPanelText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'context_window',
                                                                    'label': '上下文窗口大小',
                                                                    'placeholder': '5'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'max_retries',
                                                                    'label': 'llm请求重试次数',
                                                                    'placeholder': '3'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [
                                                            {
                                                                'component': 'VSwitch',
                                                                'props': {
                                                                    'model': 'enable_merge',
                                                                    'label': '翻译英文时合并整句'
                                                                }
                                                            }
                                                        ]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [
                                                            {
                                                                'component': 'VSwitch',
                                                                'props': {
                                                                    'model': 'enable_batch',
                                                                    'label': '启用批量翻译'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4, 'v-show': 'enable_batch'},
                                                        'content': [
                                                            {
                                                                'component': 'VTextField',
                                                                'props': {
                                                                    'model': 'batch_size',
                                                                    'label': '每批翻译行数',
                                                                    'placeholder': '10',
                                                                    'hint': '批量翻译时每次提交的字幕行数，范围1-50'
                                                                }
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'success',
                                            'variant': 'tonal'
                                        },
                                        'content': [
                                            {
                                                'component': 'span',
                                                'text': '详细说明参考：'
                                            },
                                            {
                                                'component': 'a',
                                                'props': {
                                                    'href': 'https://github.com/EllickWANG/moviepilot-plugins/blob/main/plugins.v2/autosubremoteasr/README.md',
                                                    'target': '_blank'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'u',
                                                        'text': 'README'
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "reset_tasks": False,
            "clear_history": False,
            "send_notify": False,
            "detailed_log": False,
            "retry_failed_once": False,
            "listen_transfer_event": True,
            "run_now": False,
            "path_list": "",
            "auto_scan_enabled": True,
            "auto_scan_cron": "*/10 * * * *",
            "integrity_retry_minutes": 10,
            "parallel_tasks": 1,
            "full_integrity_check": False,
            "translate_preference": "english_first",
            "translate_zh": True,
            "enable_asr": True,
            "auto_detect_language": False,
            "reuse_autosub_config": True,
            "asr_api_model": "whisper-1",
            "asr_chunk_minutes": 10,
            "asr_prompt": self.__default_asr_prompt(),
            "use_chatgpt": False,
            "openai_proxy": False,
            "compatible": False,
            "openai_url": "https://api.openai.com",
            "openai_key": None,
            "openai_model": "gpt-5-chat-latest",
            "context_window": 5,
            "max_retries": 3,
            "enable_merge": False,
            "enable_batch": True,
            "batch_size": 10,
        }

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/scan",
                "endpoint": self.api_scan,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "后台扫描媒体路径",
                "description": "按当前媒体路径配置在后台扫描新任务。",
            },
            {
                "path": "/retry_failed",
                "endpoint": self.api_retry_failed,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "重试失败任务",
                "description": "将失败任务重新加入处理队列。",
            },
            {
                "path": "/clear_completed",
                "endpoint": self.api_clear_completed,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "清理已完成任务",
                "description": "清理已完成和已忽略任务记录。",
            },
            {
                "path": "/reset_tasks",
                "endpoint": self.api_reset_tasks,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "重置任务记录",
                "description": "清理待处理、待检测、已完成、已忽略和失败任务记录，保留正在处理任务。",
            },
            {
                "path": "/clear_history",
                "endpoint": self.api_reset_tasks,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "重置任务记录（兼容旧接口）",
                "description": "兼容旧按钮地址，行为同重置任务记录。",
            },
            {
                "path": "/page_tab",
                "endpoint": self.api_page_tab,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "切换任务页签",
                "description": "切换任务记录页签。",
            },
        ]

    def __start_manual_scan(self) -> bool:
        self.__ensure_runtime_state()
        with self._auto_scan_lock:
            if self._auto_scan_thread and self._auto_scan_thread.is_alive():
                return False
            thread = threading.Thread(
                target=self.__manual_scan_worker,
                name="autosubremoteasr-manual-scan",
                daemon=True,
            )
            self._auto_scan_thread = thread
            thread.start()
            return True

    def __manual_scan_worker(self):
        logger.info(f"AI字幕手动后台扫描开始，路径数：{len(self._path_list or [])}")
        self._run_at_once(path_list=self._path_list or [])

    def api_scan(self) -> schemas.Response:
        self.__ensure_runtime_state()
        if not self._enabled:
            return schemas.Response(success=False, message="插件未启用")
        if not self._running or not self._task_queue:
            return schemas.Response(success=False, message="任务队列未启动")
        if not self._path_list:
            return schemas.Response(success=False, message="未配置媒体路径")
        if not self.__start_manual_scan():
            return schemas.Response(success=False, message="扫描任务已在后台运行")
        return schemas.Response(success=True, message="已开始后台扫描")

    def api_retry_failed(self) -> schemas.Response:
        count = self.retry_failed_tasks_once()
        return schemas.Response(success=True, message=f"已重新加入 {count} 个失败任务")

    def __clear_tasks_by_status(self, statuses: List[TaskStatus]) -> int:
        self.__ensure_runtime_state()
        status_set = set(statuses)
        with self._tasks_lock:
            tasks = self._tasks or {}
            before = len(tasks)
            self._tasks = {
                task_id: task
                for task_id, task in tasks.items()
                if task.status not in status_set
            }
            removed = before - len(self._tasks)
            if removed:
                self.save_tasks()
        return removed

    def api_clear_completed(self) -> schemas.Response:
        removed = self.__clear_tasks_by_status([TaskStatus.COMPLETED, TaskStatus.IGNORED])
        return schemas.Response(success=True, message=f"已清理 {removed} 条已完成/已忽略记录")

    def api_reset_tasks(self) -> schemas.Response:
        removed = self.reset_tasks()
        return schemas.Response(success=True, message=f"已重置任务记录，移除 {removed} 条，保留正在处理任务")

    def api_page_tab(self, payload: Optional[Dict[str, Any]] = None) -> schemas.Response:
        tab = (payload or {}).get("tab") or "queue"
        if tab not in {"processing", "pending", "waiting_check", "completed", "failed"}:
            tab = "processing"
        self.save_data("page_tab", tab)
        return schemas.Response(success=True, message="已切换任务页签")

    @staticmethod
    def get_dashboard_meta() -> Optional[List[Dict[str, str]]]:
        return None

    def get_dashboard(self, key: str = "progress", **kwargs):
        return {}, {"border": False}, []
        tasks = sorted(
            self.load_tasks().values(),
            key=lambda item: item.add_time,
            reverse=True
        )
        counts = {
            TaskStatus.PENDING: 0,
            TaskStatus.IN_PROGRESS: 0,
            TaskStatus.WAITING_FILE: 0,
            TaskStatus.COMPLETED: 0,
            TaskStatus.IGNORED: 0,
            TaskStatus.FAILED: 0,
        }
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        processing_tasks = [
            task for task in tasks
            if task.status == TaskStatus.IN_PROGRESS
        ][:8]

        elements = [
            {
                "component": "VRow",
                "content": [
                    self.__dashboard_metric("处理中", counts.get(TaskStatus.IN_PROGRESS, 0), "warning"),
                    self.__dashboard_metric("等待中", counts.get(TaskStatus.PENDING, 0), "info"),
                    self.__dashboard_metric("等待文件", counts.get(TaskStatus.WAITING_FILE, 0), "info"),
                    self.__dashboard_metric("已完成", counts.get(TaskStatus.COMPLETED, 0), "success"),
                ]
            },
            self.__dashboard_task_list(processing_tasks)
        ]
        return (
            {"cols": 12, "md": 6},
            {
                "refresh": 2,
                "border": True,
                "title": "AI字幕任务进度",
                "subtitle": f"队列 {counts.get(TaskStatus.PENDING, 0)} · 处理中 {counts.get(TaskStatus.IN_PROGRESS, 0)} · 翻译并发 {self._translate_concurrency}",
            },
            elements
        )

    @staticmethod
    def __dashboard_metric(title: str, value: int, color: str) -> dict:
        return {
            "component": "VCol",
            "props": {"cols": 6, "md": 3},
            "content": [
                {
                    "component": "VSheet",
                    "props": {
                        "class": "pa-3 rounded border",
                        "color": "transparent",
                    },
                    "content": [
                        {
                            "component": "div",
                            "props": {"class": "text-caption text-medium-emphasis"},
                            "text": title
                        },
                        {
                            "component": "div",
                            "props": {"class": f"text-h6 text-{color}"},
                            "text": str(value)
                        }
                    ]
                }
            ]
        }

    @staticmethod
    def __task_update_age_seconds(task: TaskItem) -> Optional[int]:
        if not task or not task.progress_updated:
            return None
        try:
            return max(0, int((datetime.now() - task.progress_updated).total_seconds()))
        except Exception:
            return None

    @classmethod
    def __task_waiting_hint(cls, task: TaskItem) -> Optional[str]:
        age = cls.__task_update_age_seconds(task)
        if age is None or age < 180 or task.status != TaskStatus.IN_PROGRESS:
            return None
        minutes = max(1, age // 60)
        if task.progress_stage and "翻译" in task.progress_stage:
            return f"接口等待约 {minutes} 分钟"
        if task.progress_stage and "ASR" in task.progress_stage:
            return f"ASR接口等待约 {minutes} 分钟"
        return f"超过 {minutes} 分钟未更新"

    def __dashboard_task_list(self, tasks: List[TaskItem]) -> dict:
        if not tasks:
            return {
                "component": "VAlert",
                "props": {"type": "info", "variant": "tonal", "density": "compact"},
                "text": "暂无正在处理任务"
            }

        status_text = {
            TaskStatus.PENDING: "等待中",
            TaskStatus.IN_PROGRESS: "处理中",
            TaskStatus.WAITING_FILE: "等待文件完整",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.IGNORED: "已忽略",
            TaskStatus.FAILED: "失败"
        }
        status_color = {
            TaskStatus.PENDING: "info",
            TaskStatus.IN_PROGRESS: "warning",
            TaskStatus.WAITING_FILE: "info",
            TaskStatus.COMPLETED: "success",
            TaskStatus.IGNORED: "grey",
            TaskStatus.FAILED: "error"
        }
        items = []
        for task in tasks:
            progress = self.__clip_progress(task.progress)
            title = os.path.basename(task.video_file) or task.video_file
            detail_parts = [status_text.get(task.status, task.status.value)]
            if task.progress_stage:
                detail_parts.append(task.progress_stage)
            if task.progress_detail:
                detail_parts.append(task.progress_detail[:80])
            waiting_hint = self.__task_waiting_hint(task)
            if waiting_hint:
                detail_parts.append(waiting_hint)
            items.append({
                "component": "div",
                "props": {"class": "py-2"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "d-flex align-center justify-space-between ga-2"},
                        "content": [
                            {
                                "component": "div",
                                "props": {
                                    "class": "text-body-2 text-truncate",
                                    "style": "max-width:360px"
                                },
                                "text": title
                            },
                            {
                                "component": "span",
                                "props": {"class": "text-caption text-no-wrap"},
                                "text": f"{progress:.1f}%"
                            }
                        ]
                    },
                    {
                        "component": "VProgressLinear",
                        "props": {
                            "model-value": progress,
                            "height": 6,
                            "rounded": True,
                            "color": status_color.get(task.status, "info"),
                            "class": "mt-1"
                        }
                    },
                    {
                        "component": "div",
                        "props": {"class": "text-caption text-medium-emphasis text-truncate mt-1"},
                        "text": " · ".join(detail_parts)
                    }
                ]
            })
        return {
            "component": "div",
            "props": {"class": "mt-2"},
            "content": items
        }

    @staticmethod
    def __page_action_button(text: str, icon: str, color: str, api: str) -> dict:
        return {
            "component": "VBtn",
            "props": {
                "variant": "tonal",
                "color": color,
                "prepend-icon": icon,
                "size": "small",
                "class": "autosub-asr-action-btn",
            },
            "text": text,
            "events": {
                "click": {
                    "api": api,
                    "method": "post",
                }
            },
        }

    @staticmethod
    def __status_label(status: TaskStatus) -> str:
        return {
            TaskStatus.PENDING: "等待中",
            TaskStatus.IN_PROGRESS: "处理中",
            TaskStatus.WAITING_FILE: "等待文件",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.IGNORED: "已忽略",
            TaskStatus.FAILED: "失败",
        }.get(status, str(status))

    @staticmethod
    def __status_color(status: TaskStatus) -> str:
        return {
            TaskStatus.PENDING: "info",
            TaskStatus.IN_PROGRESS: "warning",
            TaskStatus.WAITING_FILE: "info",
            TaskStatus.COMPLETED: "success",
            TaskStatus.IGNORED: "grey",
            TaskStatus.FAILED: "error",
        }.get(status, "info")

    @staticmethod
    def __format_time(value: Optional[datetime]) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"

    @staticmethod
    def __task_recent_time(task: TaskItem) -> datetime:
        values = [value for value in (task.progress_updated, task.complete_time, task.add_time) if value]
        return max(values) if values else datetime.min

    @staticmethod
    def __task_source_label(source: TaskSource) -> str:
        return {
            TaskSource.MANUAL: "手动添加",
            TaskSource.EVENT: "入库触发",
            TaskSource.AUTO_SCAN: "定时扫描",
        }.get(source, str(source))

    def __page_metric(self, title: str, value: int, color: str) -> dict:
        return {
            "component": "VCol",
            "props": {"cols": 6, "md": 2, "class": "autosub-asr-metric-col"},
            "content": [
                {
                    "component": "VSheet",
                    "props": {"class": "autosub-asr-metric pa-3 rounded border", "color": "transparent"},
                    "content": [
                        {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": title},
                        {"component": "div", "props": {"class": f"text-h6 text-{color}"}, "text": str(value)},
                    ],
                }
            ],
        }

    def __progress_block(self, task: TaskItem) -> List[dict]:
        progress = self.__clip_progress(task.progress)
        color = self.__status_color(task.status)
        detail_parts = [task.progress_stage or self.__status_label(task.status)]
        if task.progress_detail and task.progress_detail not in detail_parts:
            detail_parts.append(task.progress_detail)
        waiting_hint = self.__task_waiting_hint(task)
        if waiting_hint:
            detail_parts.append(waiting_hint)
        if task.progress_updated:
            detail_parts.append(f"更新 {task.progress_updated.strftime('%H:%M:%S')}")
        return [
            {
                "component": "div",
                "props": {"class": "autosub-asr-progress d-flex align-center"},
                "content": [
                    {
                        "component": "VProgressLinear",
                        "props": {
                            "model-value": progress,
                            "height": 8,
                            "rounded": True,
                            "color": color,
                            "style": "min-width:96px;max-width:180px;flex:1 1 auto;",
                        },
                    },
                    {"component": "span", "props": {"class": "text-caption text-no-wrap"}, "text": f"{progress:.1f}%"},
                ],
            },
            {
                "component": "div",
                "props": {"class": "autosub-asr-progress-detail text-caption text-medium-emphasis mt-1"},
                "text": " · ".join(detail_parts),
            },
        ]

    def __processing_task_card(self, task: TaskItem) -> dict:
        filename = os.path.basename(task.video_file) or task.video_file
        return {
            "component": "VCol",
            "props": {"cols": 12, "md": 4},
            "content": [
                {
                    "component": "VSheet",
                    "props": {"class": "pa-3 rounded border", "color": "transparent"},
                    "content": [
                        {
                            "component": "div",
                            "props": {"class": "d-flex align-center justify-space-between ga-2"},
                            "content": [
                                {
                                    "component": "div",
                                    "props": {"class": "text-body-2 font-weight-medium text-truncate"},
                                    "text": filename,
                                },
                                {
                                    "component": "VChip",
                                    "props": {"size": "small", "color": "warning", "variant": "tonal"},
                                    "text": "处理中",
                                },
                            ],
                        },
                        {
                            "component": "div",
                            "props": {"class": "text-caption text-medium-emphasis text-truncate mt-1"},
                            "text": task.video_file,
                        },
                        {"component": "div", "props": {"class": "mt-3"}, "content": self.__progress_block(task)},
                    ],
                }
            ],
        }

    def __task_table_rows(self, task_list: List[TaskItem]) -> List[dict]:
        rows = []
        for task in task_list[:200]:
            filename = os.path.basename(task.video_file) or task.video_file
            existing_subtitle = self.__is_existing_subtitle_task(task)
            same_language = self.__is_same_language_skip_task(task)
            generated_subtitle = task.status == TaskStatus.COMPLETED and not same_language
            waiting_check = self.__is_waiting_file_task(task)
            pending = task.status == TaskStatus.PENDING and not waiting_check
            status_label = (
                "已存在字幕" if existing_subtitle
                else "同语言跳过" if same_language
                else "已生成字幕" if generated_subtitle
                else "待检测" if waiting_check
                else "待处理" if pending
                else self.__status_label(task.status)
            )
            status_color = (
                "success" if existing_subtitle or same_language or generated_subtitle
                else "info" if pending or waiting_check
                else self.__status_color(task.status)
            )
            terminal = task.status in [TaskStatus.COMPLETED, TaskStatus.IGNORED, TaskStatus.FAILED]
            if terminal and task.complete_time:
                time_content = [
                    {"component": "div", "props": {"class": "text-caption"}, "text": f"完成 {self.__format_time(task.complete_time)}"},
                    {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": f"添加 {self.__format_time(task.add_time)}"},
                ]
            else:
                time_content = [
                    {"component": "div", "props": {"class": "text-caption"}, "text": f"添加 {self.__format_time(task.add_time)}"},
                    {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": f"完成 {self.__format_time(task.complete_time)}"},
                ]
            rows.append({
                "component": "tr",
                "content": [
                    {
                        "component": "td",
                        "content": [
                            {"component": "div", "props": {"class": "text-body-2 font-weight-medium"}, "text": filename},
                            {
                                "component": "div",
                                "props": {
                                    "class": "text-caption text-medium-emphasis",
                                    "style": "max-width:520px;white-space:normal"
                                },
                                "text": task.video_file,
                            },
                        ],
                    },
                    {"component": "td", "text": self.__task_source_label(task.source)},
                    {
                        "component": "td",
                        "content": [
                            {
                                "component": "VChip",
                                "props": {
                                    "size": "small",
                                    "variant": "tonal",
                                    "color": status_color,
                                },
                                "text": status_label,
                            }
                        ],
                    },
                    {"component": "td", "content": self.__progress_block(task)},
                    {
                        "component": "td",
                        "content": time_content,
                    },
                ],
            })
        return rows

    def __task_table_content(self, task_list: List[TaskItem], empty_text: str) -> List[dict]:
        rows = self.__task_table_rows(task_list)
        if not rows:
            return [
                {
                    "component": "VAlert",
                    "props": {"type": "info", "variant": "tonal", "density": "compact", "class": "ma-3"},
                    "text": empty_text,
                }
            ]
        return [
            {
                "component": "VTable",
                "props": {"hover": True, "density": "comfortable", "class": "autosub-asr-table"},
                "content": [
                    {
                        "component": "thead",
                        "content": [
                            {
                                "component": "tr",
                                "content": [
                                    {"component": "th", "props": {"class": "text-start ps-4"}, "text": "文件"},
                                    {"component": "th", "props": {"class": "text-start ps-4"}, "text": "来源"},
                                    {"component": "th", "props": {"class": "text-start ps-4"}, "text": "状态"},
                                    {"component": "th", "props": {"class": "text-start ps-4"}, "text": "进度"},
                                    {"component": "th", "props": {"class": "text-start ps-4"}, "text": "时间"},
                                ],
                            }
                        ],
                    },
                    {"component": "tbody", "content": rows},
                ],
            }
        ]

    @staticmethod
    def __task_table_scroll(content: List[dict]) -> dict:
        return {
            "component": "div",
            "props": {
                "class": "autosub-asr-task-scroll",
            },
            "content": content,
        }

    @staticmethod
    def __task_tab(tab: str, text: str) -> dict:
        return {
            "component": "VTab",
            "props": {"value": tab},
            "text": text,
            "events": {
                "click": {
                    "api": "plugin/AutoSubRemoteAsr/page_tab",
                    "method": "post",
                    "params": {"tab": tab},
                }
            },
        }

    def __task_tabs(self, processing_tasks: List[TaskItem], pending_tasks: List[TaskItem],
                    waiting_check_tasks: List[TaskItem], completed_tasks: List[TaskItem],
                    failed_tasks: List[TaskItem],
                    selected_tab: str) -> dict:
        tab_data = {
            "processing": (processing_tasks, "暂无正在处理任务"),
            "pending": (pending_tasks, "暂无待处理任务"),
            "waiting_check": (waiting_check_tasks, "暂无待检测任务"),
            "completed": (completed_tasks, "暂无已完成任务"),
            "failed": (failed_tasks, "暂无已失败任务"),
        }
        selected_tab = selected_tab if selected_tab in tab_data else "processing"
        active_tasks, empty_text = tab_data[selected_tab]
        return {
            "component": "VRow",
            "content": [
                {
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [
                        {"component": "div", "props": {"class": "text-subtitle-1 font-weight-medium mb-2"}, "text": "任务记录"},
                        {
                            "component": "VSheet",
                            "props": {"class": "autosub-asr-task-shell rounded border overflow-hidden", "color": "transparent"},
                            "content": [
                                {
                                    "component": "VTabs",
                                    "props": {
                                        "model-value": selected_tab,
                                        "color": "primary",
                                        "density": "comfortable",
                                        "show-arrows": True,
                                        "class": "autosub-asr-tabs",
                                        "style": "max-height:112px;overflow-y:auto;overflow-x:auto;",
                                    },
                                    "content": [
                                        self.__task_tab("processing", f"正在处理 ({len(processing_tasks)})"),
                                        self.__task_tab("pending", f"待处理 ({len(pending_tasks)})"),
                                        self.__task_tab("waiting_check", f"待检测 ({len(waiting_check_tasks)})"),
                                        self.__task_tab("completed", f"已完成 ({len(completed_tasks)})"),
                                        self.__task_tab("failed", f"已失败 ({len(failed_tasks)})"),
                                    ],
                                },
                                {"component": "VDivider"},
                                self.__task_table_scroll(self.__task_table_content(active_tasks, empty_text)),
                            ],
                        },
                    ],
                }
            ],
        }

    def get_page(self) -> List[dict]:
        self.__repair_queue_state("页面刷新")
        tasks = sorted(
            self.load_tasks().values(),
            key=self.__task_recent_time,
            reverse=True,
        )
        counts = {status: 0 for status in TaskStatus}
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1

        processing_tasks = [task for task in tasks if task.status == TaskStatus.IN_PROGRESS]
        waiting_file_tasks = [task for task in tasks if self.__is_waiting_file_task(task)]
        pending_tasks = [
            task for task in tasks
            if task.status == TaskStatus.PENDING and not self.__is_waiting_file_task(task)
        ]
        waiting_check_tasks = waiting_file_tasks
        failed_tasks = [task for task in tasks if task.status == TaskStatus.FAILED]
        completed_tasks = [
            task for task in tasks
            if task.status == TaskStatus.COMPLETED or self.__is_existing_subtitle_task(task)
        ]
        selected_tab = self.get_data("page_tab") or "processing"
        latest_update = None
        for task in tasks:
            for value in (task.progress_updated, task.complete_time, task.add_time):
                if value and (not latest_update or value > latest_update):
                    latest_update = value

        action_row = {
            "component": "VRow",
            "props": {"class": "autosub-asr-actions"},
            "content": [
                {
                    "component": "VCol",
                    "props": {"cols": 12, "class": "autosub-asr-actions-col"},
                    "content": [
                        self.__page_action_button("立即扫描", "mdi-playlist-plus", "primary",
                                                  "plugin/AutoSubRemoteAsr/scan"),
                        self.__page_action_button("重试失败", "mdi-reload", "warning",
                                                  "plugin/AutoSubRemoteAsr/retry_failed"),
                        self.__page_action_button("清理已完成", "mdi-check-circle-outline", "secondary",
                                                  "plugin/AutoSubRemoteAsr/clear_completed"),
                        self.__page_action_button("重置", "mdi-restore", "error",
                                                  "plugin/AutoSubRemoteAsr/reset_tasks"),
                    ],
                }
            ],
        }

        summary = {
            "component": "VRow",
            "content": [
                self.__page_metric("处理中", counts.get(TaskStatus.IN_PROGRESS, 0), "warning"),
                self.__page_metric("待处理", len(pending_tasks), "info"),
                self.__page_metric("待检测", len(waiting_check_tasks), "info"),
                self.__page_metric("已失败", len(failed_tasks), "error"),
                self.__page_metric("已完成", len(completed_tasks), "success"),
                {
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [
                        {
                            "component": "VAlert",
                            "props": {"type": "info", "variant": "tonal", "density": "compact"},
                            "text": f"最近任务更新时间：{self.__format_time(latest_update)}；"
                                    f"任务并行 {self._parallel_tasks}，翻译接口并发 {self._translate_concurrency}，"
                                    f"每批 {self._batch_size} 行",
                        }
                    ],
                },
            ],
        }

        task_table = self.__task_tabs(
            processing_tasks,
            pending_tasks,
            waiting_check_tasks,
            completed_tasks,
            failed_tasks,
            selected_tab,
        )

        return [
            self.__responsive_style(),
            {
                "component": "div",
                "props": {"class": "autosub-asr-page"},
                "content": [action_row, summary, task_table],
            },
        ]

    def __legacy_get_page(self) -> List[dict]:
        # 加载任务并按添加时间倒序排列
        tasks: Dict[str, TaskItem] = self.load_tasks()
        sorted_tasks = sorted(
            tasks.items(),
            key=lambda x: x[1].add_time,
            reverse=True
        )

        status_classes = {
            TaskStatus.PENDING: "text-info",
            TaskStatus.IN_PROGRESS: "text-warning",
            TaskStatus.WAITING_FILE: "text-info",
            TaskStatus.COMPLETED: "text-success",
            TaskStatus.IGNORED: "text-muted",
            TaskStatus.FAILED: "text-error"
        }

        rows = []
        for task_id, task in sorted_tasks:
            source_label = {
                TaskSource.MANUAL: "手动添加",
                TaskSource.EVENT: "入库触发",
                TaskSource.AUTO_SCAN: "定时扫描"
            }.get(task.source, task.source)

            status_text = {
                TaskStatus.PENDING: "等待中",
                TaskStatus.IN_PROGRESS: "处理中",
                TaskStatus.WAITING_FILE: "等待文件完整",
                TaskStatus.COMPLETED: "已完成",
                TaskStatus.IGNORED: "已忽略",
                TaskStatus.FAILED: "失败"
            }.get(task.status, task.status)

            status_class = status_classes.get(task.status, "")

            add_time_str = task.add_time.strftime("%Y-%m-%d %H:%M:%S")
            complete_time_str = (
                task.complete_time.strftime("%Y-%m-%d %H:%M:%S")
                if task.complete_time else "-"
            )
            progress = self.__clip_progress(task.progress)
            progress_color = {
                TaskStatus.PENDING: "#1976D2",
                TaskStatus.IN_PROGRESS: "#F59E0B",
                TaskStatus.WAITING_FILE: "#0288D1",
                TaskStatus.COMPLETED: "#2E7D32",
                TaskStatus.IGNORED: "#757575",
                TaskStatus.FAILED: "#D32F2F"
            }.get(task.status, "#1976D2")
            progress_detail_parts = [task.progress_stage or status_text]
            if task.progress_detail and task.progress_detail not in progress_detail_parts:
                progress_detail_parts.append(task.progress_detail)
            if task.progress_updated:
                progress_detail_parts.append(task.progress_updated.strftime("%H:%M:%S"))
            progress_detail = " · ".join(progress_detail_parts)

            rows.append({
                "component": "tr",
                "props": {"class": "text-sm"},
                "content": [
                    {"component": "td", "text": add_time_str},
                    {"component": "td", "text": task.video_file},
                    {"component": "td", "text": source_label},
                    {"component": "td", "text": complete_time_str},
                    {
                        "component": "td",
                        "props": {"class": status_class},
                        "text": status_text
                    },
                    {
                        "component": "td",
                        "content": [
                            {
                                "component": "div",
                                "props": {
                                    "class": "d-flex align-center",
                                    "style": "gap:8px;min-width:180px"
                                },
                                "content": [
                                    {
                                        "component": "div",
                                        "props": {
                                            "style": "width:120px;height:8px;border-radius:999px;"
                                                     "background:rgba(125,125,125,.18);overflow:hidden"
                                        },
                                        "content": [
                                            {
                                                "component": "div",
                                                "props": {
                                                    "style": f"height:100%;width:{progress}%;"
                                                             f"background:{progress_color};border-radius:999px"
                                                }
                                            }
                                        ]
                                    },
                                    {
                                        "component": "span",
                                        "props": {"class": "text-caption text-no-wrap"},
                                        "text": f"{progress:.1f}%"
                                    }
                                ]
                            },
                            {
                                "component": "div",
                                "props": {
                                    "class": "text-caption text-muted mt-1",
                                    "style": "max-width:280px;white-space:normal"
                                },
                                "text": progress_detail
                            }
                        ]
                    },
                ],
            })

        return [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VTable",
                                "props": {"hover": True},
                                "content": [
                                    {
                                        "component": "thead",
                                        "content": [
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "添加时间"
                                            },
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "视频文件"
                                            },
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "来源"
                                            },
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "完成时间"
                                            },
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "状态"
                                            },
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "进度"
                                            },
                                        ]
                                    },
                                    {"component": "tbody", "content": rows}
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_state(self) -> bool:
        """
        获取插件状态，如果插件正在运行， 则返回True
        """
        return self._running

    def stop_service(self):
        """
        退出插件
        """
        self.__ensure_runtime_state()
        alive_threads = [thread for thread in self._consumer_threads.values() if thread and thread.is_alive()]
        if self._running or alive_threads:
            self._event.set()
        if alive_threads:
            logger.info("正在停止当前任务...")
            for thread in alive_threads:
                thread.join(timeout=15)
            alive_threads = [thread for thread in alive_threads if thread.is_alive()]
            if alive_threads:
                logger.warn(f"仍有 {len(alive_threads)} 个任务线程未退出，将在后台继续等待中断")

        if self._task_queue:
            while True:
                try:
                    self._task_queue.get_nowait()
                except queue.Empty:
                    break
                self.__safe_task_done()
            logger.info("任务队列已清空")
        with self._tasks_lock:
            self._queued_task_ids = set()
        if self._tasks is not None:
            for task_id in list(self._tasks.keys()):
                task = self._tasks[task_id]
                if task.status == TaskStatus.IN_PROGRESS:
                    task.status = TaskStatus.PENDING
                    task.complete_time = None
                    task.progress_stage = "等待重新处理"
                    task.progress_detail = "插件停止时任务未完成"
                    task.progress_updated = datetime.now()
                elif task.status in [TaskStatus.PENDING, TaskStatus.WAITING_FILE]:
                    task.complete_time = None
            self.save_tasks()  # 持久化更新后的任务列表
        self._running = False
        self._consumer_threads = {}
        self._consumer_thread = None
        self._current_processing_task = None
        self._current_processing_tasks = {}
        self._scheduled_retry_tasks = {}
        self._auto_scan_thread = None
        if not alive_threads:
            self._event.clear()
        logger.info(f"自动字幕生成服务已停止")
