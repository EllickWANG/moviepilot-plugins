import copy
import inspect
import math
import os
import random
import tempfile
import time
import traceback
from datetime import timedelta, datetime
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional
from threading import Event, RLock
import iso639
import psutil
import srt
from lxml import etree
from dataclasses import dataclass
from enum import Enum
import queue
import threading
from uuid import uuid4
from apscheduler.triggers.cron import CronTrigger
import httpx
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
    plugin_name = "AI字幕自动生成(接口ASR)"
    # 插件描述
    plugin_desc = "使用远程语音识别接口生成字幕，并复用私有版接口配置翻译中文字幕。"
    # 插件图标
    plugin_icon = "mdi-subtitles-outline"
    # 主题色
    plugin_color = "#2C4F7E"
    # 插件版本
    plugin_version = "1.0.1"
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
    _tasks_lock = None
    _progress_save_at: Dict[str, float] = None
    _scheduled_retry_tasks: Dict[str, float] = None
    _auto_scan_thread = None
    _auto_scan_lock = None
    _parallel_tasks = 1
    _cpu_threads = 2
    _full_integrity_check = False
    _asr_api_model = "whisper-1"
    _asr_chunk_minutes = 10
    _asr_chunk_seconds = 600
    _asr_random_check_rate = 0.2
    _reuse_autosub_config = True
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
    _clear_history = None
    _listen_transfer_event = None
    _send_notify = None
    _translate_preference = None
    _run_now = None
    _path_list = None
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
        if self._progress_save_at is None:
            self._progress_save_at = {}
        if self._scheduled_retry_tasks is None:
            self._scheduled_retry_tasks = {}
        if self._auto_scan_lock is None:
            self._auto_scan_lock = threading.Lock()

    @staticmethod
    def __normalize_parallel_tasks(value) -> int:
        try:
            return max(1, min(3, int(value or 1)))
        except Exception:
            return 1

    @staticmethod
    def __normalize_cpu_threads(value) -> int:
        try:
            max_threads = max(1, min(4, psutil.cpu_count(logical=False) or 1))
            return max(1, min(max_threads, int(value or 2)))
        except Exception:
            return 2

    @staticmethod
    def __normalize_retry_minutes(value) -> int:
        try:
            return max(5, min(1440, int(value or 10)))
        except Exception:
            return 10

    @staticmethod
    def __normalize_asr_chunk_minutes(value) -> int:
        try:
            return max(1, min(30, int(value or 10)))
        except Exception:
            return 10

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
            "compatible": bool(source_config.get("compatible", False)),
            "source": source_name,
        }

    def __resolve_openai_settings(self, config: dict) -> bool:
        self._openai_api_key = None
        self._openai_api_url = None
        self._openai_api_proxy = False
        self._openai_api_compatible = False
        self._openai_model = None

        sources = []
        if self._reuse_autosub_config:
            sources.append((self.get_config("AutoSubv2Plus"), "AI字幕自动生成(v2) 私有版"))
        sources.append((config, "当前插件"))

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

    def __create_openai_http_client(self, timeout: int = 300) -> httpx.Client:
        proxy_url = None
        if self._openai_api_proxy:
            proxy_config = settings.PROXY or {}
            proxy_url = proxy_config.get("https") or proxy_config.get("http")
        client_kwargs = {"timeout": timeout}
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
        self.__ensure_runtime_state()
        self._tasks = self.load_tasks()
        self._progress_save_at = {}
        self._enabled = config.get('enabled', False)
        self._clear_history = config.get('clear_history', False)
        self._listen_transfer_event = config.get('listen_transfer_event', True)
        self._run_now = config.get('run_now')
        self._path_list = self.__normalize_path_list(config.get('path_list'))
        self._send_notify = config.get('send_notify', False)
        self._parallel_tasks = self.__normalize_parallel_tasks(config.get('parallel_tasks', 1))
        self._cpu_threads = self.__normalize_cpu_threads(config.get('cpu_threads', 2))
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
        self._reuse_autosub_config = bool(config.get('reuse_autosub_config', True))
        self._auto_detect_language = config.get('auto_detect_language', False)
        self._translate_zh = config.get('translate_zh', False)
        self._enable_batch = config.get('enable_batch', True)
        self._batch_size = int(config.get('batch_size')) if config.get('batch_size') else 10
        self._context_window = int(config.get('context_window')) if config.get('context_window') else 5
        self._max_retries = int(config.get('max_retries')) if config.get('max_retries') else 3
        self._enable_merge = config.get('enable_merge', False)
        self._openai = None

        if self._clear_history:
            config['clear_history'] = False
            self.update_config(config)
            self.clear_tasks()

        if not self._enabled and not self._run_now:
            self.stop_service()
            return

        api_required = self._translate_zh or self._enable_asr
        if api_required and not self.__resolve_openai_settings(config):
            logger.error("接口ASR或中文字幕翻译需要大模型接口配置，请复用私有版配置或在当前插件中维护接口")
            return

        if self._translate_zh:
            self._openai = OpenAi(api_key=self._openai_api_key, api_url=self._openai_api_url,
                                  proxy=settings.PROXY if self._openai_api_proxy else None,
                                  model=self._openai_model, compatible=bool(self._openai_api_compatible))

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
            if started_now:
                self.__enqueue_existing_tasks()

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
                self._task_queue.put(task)
            elif task.status == TaskStatus.WAITING_FILE:
                if task.next_retry_time and task.next_retry_time > now:
                    self.__schedule_waiting_file_retry(task)
                else:
                    self._task_queue.put(task)
        if changed:
            self.save_tasks()

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
        self._task_queue.put(task)
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

    def load_tasks(self) -> Dict[str, TaskItem]:
        raw_tasks = self.get_data("tasks") or {}
        tasks = {}
        for task_id, task_dict in raw_tasks.items():
            try:
                status = TaskStatus(task_dict["status"])
                progress_stage = task_dict.get("progress_stage") or ""
                progress_detail = task_dict.get("progress_detail") or ""
                if status == TaskStatus.FAILED and (
                        self.__is_incomplete_video_error(progress_detail)
                        or progress_stage == "视频不完整"
                        or progress_stage == "服务已停止"
                        or "插件停止时任务未完成" in progress_detail
                ):
                    status = TaskStatus.WAITING_FILE if self.__is_incomplete_video_error(progress_detail) else TaskStatus.PENDING
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

        self._task_queue.put(task)
        with self._tasks_lock:
            self._tasks[task.task_id] = task
            self.save_tasks()
        logger.info(f"加入任务队列: {video_file}")
        return True

    def clear_tasks(self):
        self.__ensure_runtime_state()
        with self._tasks_lock:
            self._tasks = {task_id: task for task_id, task in self._tasks.items() if task.status in [
                TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.WAITING_FILE
            ]}
            self.save_tasks()
        logger.info("插件历史任务已清除")

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

    def __is_duplicate_task(self, video_file: str) -> bool:
        self.__ensure_runtime_state()
        with self._tasks_lock:
            for task in (self._tasks or {}).values():
                if task.video_file == video_file and task.status in [
                    TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.WAITING_FILE
                ]:
                    return True
        return False

    def _consume_tasks(self, worker_index: int = 1):
        while not self._event.is_set():
            if worker_index > self._parallel_tasks:
                break
            task = None
            try:
                task = self._task_queue.get(timeout=1)
                if task is None:
                    self._task_queue.task_done()
                    continue
                with self._tasks_lock:
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
                task.complete_time = datetime.now() if task.status in [
                    TaskStatus.COMPLETED, TaskStatus.IGNORED, TaskStatus.FAILED
                ] else None
                if task.status == TaskStatus.COMPLETED:
                    self.__update_task_progress(task, 100, "处理完成", "字幕处理完成", force=True)
                elif task.status == TaskStatus.IGNORED:
                    self.__update_task_progress(task, 100, "已忽略", task.progress_detail or "任务已忽略", force=True)
                elif task.status == TaskStatus.FAILED:
                    self.__update_task_progress(task, task.progress, "处理失败",
                                                task.progress_detail or "任务处理失败", force=True)
                elif task.status == TaskStatus.WAITING_FILE:
                    self.__schedule_waiting_file_retry(task)
                with self._tasks_lock:
                    self._tasks[task.task_id] = task
                    self.save_tasks()
                self._task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"消费任务时发生异常: {e}")
                logger.error(traceback.format_exc())
                if task:
                    task.status = TaskStatus.FAILED
                    task.complete_time = datetime.now()
                    self.__update_task_progress(task, task.progress, "处理异常", str(e)[:120], force=True)
                    try:
                        self._task_queue.task_done()
                    except ValueError:
                        pass
            finally:
                if task:
                    with self._tasks_lock:
                        self._current_processing_tasks.pop(task.task_id, None)
                        if self._current_processing_task and self._current_processing_task.task_id == task.task_id:
                            self._current_processing_task = None
                        self._progress_save_at.pop(task.task_id, None)
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
                self.add_task(file_path, TaskSource.EVENT)

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
                for video_file in self.__get_library_files(path):
                    if self._event.is_set():
                        break
                    result = self.__scan_video_file_for_task(video_file)
                    stats[result] = stats.get(result, 0) + 1
        except Exception as e:
            logger.error(f"AI字幕{reason}异常：{e}")
            logger.error(traceback.format_exc())
        finally:
            seconds = round(time.time() - start_time, 2)
            logger.info(
                f"AI字幕{reason}完成：新增 {stats['added']}，重新排队 {stats['requeued']}，"
                f"等待完整 {stats['waiting']}，已在队列 {stats['active']}，已处理 {stats['done']}，"
                f"已有字幕 {stats['subtitle_exists']}，失败跳过 {stats['failed']}，"
                f"无效路径 {stats['invalid']}，其他跳过 {stats['skipped']}，耗时 {seconds} 秒"
            )

    def __scan_video_file_for_task(self, video_file: str) -> str:
        now = datetime.now()
        latest_task = self.__find_latest_task_by_video_file(video_file)
        if latest_task:
            if latest_task.status in [TaskStatus.PENDING, TaskStatus.IN_PROGRESS]:
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

        if self.__target_subtitle_exists(video_file):
            return "subtitle_exists"

        return "added" if self.add_task(video_file, TaskSource.AUTO_SCAN) else "skipped"

    def _run_at_once(self, path_list: List[str]):
        # 依次处理每个目录
        for path in path_list:
            if not os.path.exists(path) or not os.path.isabs(path):
                logger.warn(f"目录/文件无效，不进行处理:{path}")
                continue
            if os.path.isdir(path):
                for video_file in self.__get_library_files(path):
                    self.add_task(video_file, TaskSource.MANUAL)
            elif os.path.splitext(path)[-1].lower() in settings.RMT_MEDIAEXT:
                self.add_task(path, TaskSource.MANUAL)

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
            if self.__target_subtitle_exists(video_file):
                logger.warn(f"字幕文件已经存在，不进行处理")
                self.__update_task_progress(task, 100, "已忽略", "目标字幕已存在", force=True)
                return TaskStatus.IGNORED
            if not self.__check_video_integrity(video_file, task):
                return TaskStatus.WAITING_FILE if task and task.status == TaskStatus.WAITING_FILE else TaskStatus.FAILED
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
                message += f"字幕翻译语言: zh\n "
            message += f"耗时：{round(end_time - start_time, 2)}秒"
            logger.info(f"自动字幕生成 处理完成：{message}")
            if self._send_notify:
                self.post_message(mtype=NotificationType.Plugin, title="【自动字幕生成】", text=message)
            return TaskStatus.COMPLETED
        except UserInterruptException:
            logger.info(f"用户中断当前任务：{video_file}")
            self.__update_task_progress(task, task.progress if task else 0, "等待重新处理",
                                        "用户中断当前任务", force=True)
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

    def __transcribe_audio_chunk(self, audio_file: str, audio_lang: str) -> dict:
        request_lang = self.__normalize_language_code(audio_lang, fallback="")
        data = {
            "model": self._asr_api_model,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if request_lang:
            data["language"] = request_lang

        with self.__create_openai_http_client(timeout=300) as client:
            with open(audio_file, "rb") as file_obj:
                response = client.post(
                    f"{self.__get_openai_base_url()}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._openai_api_key}"},
                    data=data,
                    files={"file": (os.path.basename(audio_file), file_obj, "audio/mpeg")},
                )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            raise RuntimeError(f"接口ASR返回错误 {response.status_code}: {response.text[:500]}") from err
        try:
            return response.json()
        except ValueError as err:
            raise RuntimeError(f"接口ASR返回非JSON响应: {response.text[:500]}") from err

    @staticmethod
    def __get_audio_file_duration(audio_file: str) -> float:
        meta = Ffmpeg().get_video_metadata(audio_file)
        try:
            return float((meta or {}).get("format", {}).get("duration") or 0)
        except Exception:
            return 0.0

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
        duration = float(response.get("duration") or 0) or self.__get_audio_file_duration(chunk_file)
        if duration <= 0:
            duration = self._asr_chunk_seconds
        subs.append(srt.Subtitle(
            index=len(subs) + 1,
            start=timedelta(seconds=max(0, offset)),
            end=timedelta(seconds=max(0, offset + duration)),
            content=text
        ))

    def __do_speech_recognition(self, video_file: str, audio_index: int, audio_lang: str, audio_dir: str,
                                expected_chunks: int, task: Optional[TaskItem] = None):
        """
        流水线调用远程语音识别接口生成字幕。
        :param audio_lang:
        :return:
        """
        lang = audio_lang
        try:
            self.__update_task_progress(task, 34, "接口语音识别",
                                        f"模型：{self._asr_api_model}，预计 {expected_chunks} 段", force=True)
            subs = []
            detected_lang = None
            processed_chunks = 0

            def handle_chunk(chunk_file: str):
                nonlocal detected_lang, processed_chunks
                if self._event.is_set():
                    logger.info("接口ASR服务停止")
                    raise UserInterruptException("用户中断当前任务")
                chunk_no = self.__get_chunk_no(chunk_file)
                checked = self.__should_check_asr_chunk(chunk_no, expected_chunks)
                self.__validate_audio_chunk(chunk_file, chunk_no, expected_chunks, force=checked)
                percent = self.__scale_progress(24, 72, processed_chunks, expected_chunks)
                self.__update_task_progress(task, percent, "提取并识别音频",
                                            f"第 {chunk_no}/{expected_chunks or '?'} 段正在上传ASR")
                response = self.__transcribe_audio_chunk(chunk_file, lang)
                self.__validate_asr_response(response, chunk_no, expected_chunks, checked=checked)
                detected_lang = detected_lang or response.get("language")
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
                if error and not error.startswith("处理音频分段失败"):
                    self.__mark_waiting_file(task, error or "提取音频失败，可能文件仍未完整")
                return False, None, []
            if chunk_count <= 0:
                logger.error("接口ASR没有可识别的音频分段")
                return False, None, []

            final_lang = self.__normalize_language_code(
                detected_lang if lang == "auto" else lang,
                fallback="und"
            )
            if not subs:
                logger.info("音频文件中未检测到任何语言内容，生成空字幕文件以避免重复处理")
            self.__update_task_progress(task, 72, "语音识别完成", f"识别到 {len(subs)} 条字幕", force=True)
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
            logger.info("已开启自动语言检测，将由接口ASR自动识别语言")
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
                task
            )
            if ret:
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
        if not os.path.isdir(in_path):
            yield in_path
            return

        for root, dirs, files in os.walk(in_path):
            if exclude_path and any(os.path.abspath(root).startswith(os.path.abspath(path))
                                    for path in exclude_path.split(",")):
                continue

            for file in files:
                cur_path = os.path.join(root, file)
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

    def __translate_to_zh(self, text: str, context: str = None) -> str:
        if self._event.is_set():
            raise UserInterruptException("用户中断当前任务")
        return self._openai.translate_to_zh(text, context, max_retries=self._max_retries)

    def __process_batch(self, all_subs: list, batch: list, stats: dict) -> list:
        """批量处理逻辑"""
        indices = [all_subs.index(item) for item in batch]
        context = self.__get_context(all_subs, indices, is_batch=True) if self._context_window > 0 else None
        batch_text = '\n'.join([item.content for item in batch])

        try:
            ret, result = self.__translate_to_zh(batch_text, context)
            if not ret:
                raise Exception(result)

            translated = [line.strip() for line in result.split('\n') if line.strip()]
            if len(translated) != len(batch):
                raise Exception(f"批次行数不匹配 {len(translated)}/{len(batch)}")

            for item, trans in zip(batch, translated):
                item.content = f"{trans}\n{item.content}"
            stats['batch_success'] += len(batch)
            return batch
        except Exception as e:
            logger.warning(f"批次翻译失败（{str(e)}），降级到单行匹配...")
            stats['batch_fail'] += 1
            return [self.__process_single(all_subs, item, stats) for item in batch]

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
        processed = []
        current_batch = []

        for item in valid_subs:
            current_batch.append(item)

            if len(current_batch) >= self._batch_size:
                processed += self.__process_items(valid_subs, current_batch, stats)
                current_batch = []
                logger.info(f"进度: {len(processed)}/{len(valid_subs)}")
                percent = self.__scale_progress(76, 98, len(processed), len(valid_subs))
                self.__update_task_progress(task, percent, "翻译字幕",
                                            f"{len(processed)}/{len(valid_subs)} 行")

        if current_batch:
            processed += self.__process_items(valid_subs, current_batch, stats)
            percent = self.__scale_progress(76, 98, len(processed), len(valid_subs))
            self.__update_task_progress(task, percent, "翻译字幕",
                                        f"{len(processed)}/{len(valid_subs)} 行", force=True)

        translated_count = stats['batch_success'] + stats['line_fallback']
        if stats['total'] > 0 and translated_count <= 0:
            logger.error("字幕翻译全部失败，不生成中文字幕文件")
            self.__update_task_progress(task, task.progress if task else 76, "翻译失败",
                                        f"全部 {stats['total']} 行翻译失败", force=True)
            return False

        self.__save_srt(dest_subtitle, processed)
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

    def __target_subtitle_exists(self, video_file):
        """
        目标字幕文件是否存在
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

        exist, lang, _ = self.__external_subtitle_exists(video_file, prefer_langs, strict=strict)
        if exist:
            return True

        video_meta = Ffmpeg().get_video_metadata(video_file)
        if not video_meta:
            return False
        ret, subtitle_index, subtitle_lang = self.__get_video_prefer_subtitle(video_meta, prefer_lang=prefer_langs,
                                                                              only_srt=False)
        if ret and (not prefer_langs or subtitle_lang in prefer_langs):
            return True

        return False

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
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
                                            'model': 'clear_history',
                                            'label': '清理历史记录',
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cpu_threads',
                                            'label': 'CPU线程上限',
                                            'placeholder': '2',
                                            'hint': '限制ffmpeg音频提取线程，建议1-2'
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
                                            'hint': '同一时间处理的字幕任务数量，范围1-3'
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
                                            'hint': '分段上传，降低单次接口超时风险'
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
                                                        'props': {'cols': 12, 'md': 4},
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
                                                            'md': 4,
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
                                                            'md': 4,
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
                                                                    'placeholder': 'gpt-4o-mini',
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
                                                                    'placeholder': '10'
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
            "clear_history": False,
            "send_notify": False,
            "listen_transfer_event": True,
            "run_now": False,
            "path_list": "",
            "auto_scan_enabled": True,
            "auto_scan_cron": "*/10 * * * *",
            "integrity_retry_minutes": 10,
            "parallel_tasks": 1,
            "cpu_threads": 2,
            "full_integrity_check": False,
            "translate_preference": "english_first",
            "translate_zh": True,
            "enable_asr": True,
            "auto_detect_language": False,
            "reuse_autosub_config": True,
            "asr_api_model": "whisper-1",
            "asr_chunk_minutes": 10,
            "use_chatgpt": False,
            "openai_proxy": False,
            "compatible": False,
            "openai_url": "https://api.openai.com",
            "openai_key": None,
            "openai_model": "gpt-4o-mini",
            "context_window": 5,
            "max_retries": 3,
            "enable_merge": False,
            "enable_batch": True,
            "batch_size": 10,
        }

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    @staticmethod
    def get_dashboard_meta() -> Optional[List[Dict[str, str]]]:
        return [{
            "key": "progress",
            "name": "AI字幕进度"
        }]

    def get_dashboard(self, key: str = "progress", **kwargs):
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
        active_tasks = [
            task for task in tasks
            if task.status in [TaskStatus.IN_PROGRESS, TaskStatus.PENDING, TaskStatus.WAITING_FILE]
        ][:8]
        if not active_tasks:
            active_tasks = tasks[:8]

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
            self.__dashboard_task_list(active_tasks)
        ]
        return (
            {"cols": 12, "md": 6},
            {
                "refresh": 5,
                "border": True,
                "title": "AI字幕任务进度",
                "subtitle": f"队列 {counts.get(TaskStatus.PENDING, 0)} · 处理中 {counts.get(TaskStatus.IN_PROGRESS, 0)}",
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

    def __dashboard_task_list(self, tasks: List[TaskItem]) -> dict:
        if not tasks:
            return {
                "component": "VAlert",
                "props": {"type": "info", "variant": "tonal", "density": "compact"},
                "text": "暂无任务"
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

    def get_page(self) -> List[dict]:
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
            while not self._task_queue.empty():
                self._task_queue.get_nowait()
                self._task_queue.task_done()
            logger.info("任务队列已清空")
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
