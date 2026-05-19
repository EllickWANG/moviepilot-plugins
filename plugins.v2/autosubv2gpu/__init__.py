import copy
import importlib.metadata
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import traceback
import wave
from datetime import timedelta, datetime
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional
from threading import Event
import iso639
import psutil
import srt
from lxml import etree
from dataclasses import dataclass
from enum import Enum
import queue
import threading
from uuid import uuid4
from fastapi import Body
from app import schemas
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event as MPEvent
from app.schemas import TransferInfo
from app.schemas.types import NotificationType, EventType
from app.log import logger
from app.plugins import _PluginBase
from app.utils.system import SystemUtils
from .ffmpeg import Ffmpeg
from .translate.openai_translate import OpenAi


class UserInterruptException(Exception):
    """用户中断当前任务的异常"""
    pass


class TaskSource(Enum):
    MANUAL = "manual"
    EVENT = "event"


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
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


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class AutoSubv2Gpu(_PluginBase):
    # 插件名称
    plugin_name = "AI字幕自动生成(v2) GPU版"
    # 插件描述
    plugin_desc = "使用外部 ASR 后端自动生成视频字幕，并使用大模型翻译字幕成中文。"
    # 插件图标
    plugin_icon = "mdi-subtitles-outline"
    # 主题色
    plugin_color = "#2C4F7E"
    # 插件版本
    plugin_version = "2.7.2"
    # 插件作者
    plugin_author = "Ellick"
    # 作者主页
    author_url = "https://github.com/EllickWANG"
    # 插件配置项ID前缀
    plugin_config_prefix = "autosubv2gpu"
    # 加载顺序
    plugin_order = 14
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _instance: Optional["AutoSubv2Gpu"] = None
    _tasks: Dict[str, TaskItem] = None
    _task_queue = None
    _consumer_thread = None
    _current_processing_task = None
    _running = False
    _event = Event()
    _enabled = None
    _clear_history = None
    _listen_transfer_event = None
    _send_notify = None
    _translate_preference = None
    _run_now = None
    _path_list = None
    _file_size = None
    _translate_zh = None
    _openai = None
    _enable_batch = None
    _batch_size = None
    _context_window = None
    _max_retries = None
    _enable_merge = None
    _enable_asr = None
    _asr_backend = None
    _auto_detect_language = None
    _external_asr_command = None
    _external_asr_timeout = None
    _external_asr_default_language = None
    _openvino_model_id = None
    _openvino_model_path = None
    _openvino_device = None
    _openvino_auto_download = None
    _openvino_max_new_tokens = None
    _latest_asr_test: Dict[str, Any] = None
    _huggingface_proxy = None
    _faster_whisper_model_path = None
    _faster_whisper_model = None

    def init_plugin(self, config=None):
        self.__class__._instance = self
        # 如果没有配置信息， 则不处理
        if not config:
            return
        self._tasks = self.load_tasks()
        self._enabled = config.get('enabled', False)
        self._clear_history = config.get('clear_history', False)
        self._listen_transfer_event = config.get('listen_transfer_event', True)
        self._run_now = config.get('run_now')
        if self._run_now:
            self._path_list = list(set(config.get('path_list').split('\n')))
        self._send_notify = config.get('send_notify', False)
        self._file_size = int(config.get('file_size')) if config.get('file_size') else 10
        # 字幕生成设置
        self._translate_preference = config.get('translate_preference', 'english_first')
        self._enable_asr = config.get('enable_asr', True)
        self._asr_backend = config.get('asr_backend', 'openvino_genai')
        if self._asr_backend not in ['openvino_genai', 'external_command', 'faster_whisper']:
            self._asr_backend = 'openvino_genai'
        if self._enable_asr:
            self._auto_detect_language = config.get('auto_detect_language', False)
            self._external_asr_command = str(config.get('external_asr_command') or '').strip()
            self._external_asr_timeout = int(config.get('external_asr_timeout')) \
                if config.get('external_asr_timeout') else 7200
            self._external_asr_default_language = str(config.get('external_asr_default_language') or 'en').strip()
            self._openvino_model_id = str(config.get('openvino_model_id') or 'OpenVINO/whisper-base-int8-ov').strip()
            self._openvino_model_path = str(
                config.get('openvino_model_path') or (
                    self.get_data_path() / "openvino-models" / self.__safe_model_dir(self._openvino_model_id)
                )
            )
            self._openvino_device = str(config.get('openvino_device') or 'GPU').strip().upper()
            self._openvino_auto_download = config.get('openvino_auto_download', True)
            self._openvino_max_new_tokens = int(config.get('openvino_max_new_tokens')) \
                if config.get('openvino_max_new_tokens') else 448
            self._latest_asr_test = (
                config.get('latest_asr_test') if isinstance(config.get('latest_asr_test'), dict) else {}
            )
            self._faster_whisper_model = config.get('faster_whisper_model', 'base')
            self._faster_whisper_model_path = config.get('faster_whisper_model_path',
                                                         self.get_data_path() / "faster-whisper-models")
            self._huggingface_proxy = config.get('proxy', True)
        self._translate_zh = config.get('translate_zh', False)

        if self._clear_history:
            config['clear_history'] = False
            self.update_config(config)
            self.clear_tasks()

        if not self._enabled and not self._run_now:
            self.stop_service()
            return

        if self._translate_zh:
            use_chatgpt = config.get('use_chatgpt', True)
            if use_chatgpt:
                chatgpt = self.get_config("ChatGPT")
                if not chatgpt:
                    logger.error(f"翻译依赖于ChatGPT，请先维护ChatGPT插件")
                    return
                openai_key_str = chatgpt and chatgpt.get("openai_key")
                openai_url = chatgpt and chatgpt.get("openai_url")
                openai_proxy = chatgpt and chatgpt.get("proxy")
                openai_model = chatgpt and chatgpt.get("model")
                compatible = chatgpt and chatgpt.get("compatible")
                if not openai_key_str:
                    logger.error(f"请先在ChatGPT插件中维护openai_key")
                    return
                openai_key = [key.strip() for key in openai_key_str.split(',') if key.strip()][0]
            else:
                openai_key = config.get('openai_key')
                if not openai_key:
                    logger.error(f"翻译依赖于OpenAI，请先维护openai_key")
                    return
                openai_url = config.get('openai_url', "https://api.openai.com")
                openai_proxy = config.get('openai_proxy', False)
                openai_model = config.get('openai_model', "gpt-3.5-turbo")
                compatible = config.get('compatible', False)
            self._openai = OpenAi(api_key=openai_key, api_url=openai_url,
                                  proxy=settings.PROXY if openai_proxy else None,
                                  model=openai_model, compatible=bool(compatible))
            self._enable_batch = config.get('enable_batch', True)
            self._batch_size = int(config.get('batch_size')) if config.get('batch_size') else 10
            self._context_window = int(config.get('context_window')) if config.get('context_window') else 5
            self._max_retries = int(config.get('max_retries')) if config.get('max_retries') else 3
            self._enable_merge = config.get('enable_merge', False)

        if self._enabled:
            logger.info("AI生成字幕服务已启动")
            # asr 配置检查
            if self._enable_asr and not self.__check_asr():
                return

            if not self._running:
                self._task_queue = queue.Queue()
                self._consumer_thread = threading.Thread(target=self._consume_tasks, daemon=True)
                self._consumer_thread.start()
                logger.info("任务队列和消费者线程已启动")
                self._running = True

            if self._run_now:
                config['run_now'] = False
                self.update_config(config)
                logger.info("立即运行一次")
                self._run_at_once(path_list=self._path_list)
        else:
            self.stop_service()

    def load_tasks(self) -> Dict[str, TaskItem]:
        raw_tasks = self.get_data("tasks") or {}
        tasks = {}
        for task_id, task_dict in raw_tasks.items():
            try:
                task = TaskItem(
                    task_id=task_dict["task_id"],
                    video_file=task_dict["video_file"],
                    source=TaskSource(task_dict["source"]),
                    add_time=datetime.fromisoformat(task_dict["add_time"]),
                    status=TaskStatus(task_dict["status"]),
                    complete_time=datetime.fromisoformat(task_dict["complete_time"])
                    if task_dict.get("complete_time") else None,
                )
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
        }

    def save_tasks(self):
        tasks_dict = {task_id: self._serialize_task(task) for task_id, task in self._tasks.items()}
        self.save_data("tasks", tasks_dict)

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

        self._task_queue.put(task)
        self._tasks[task.task_id] = task
        self.save_tasks()
        logger.info(f"加入任务队列: {video_file}")
        return True

    def clear_tasks(self):
        self._tasks = {task_id: task for task_id, task in self._tasks.items() if task.status in [
            TaskStatus.PENDING, TaskStatus.IN_PROGRESS
        ]}
        self.save_tasks()
        logger.info("插件历史任务已清除")

    def __is_duplicate_task(self, video_file: str) -> bool:
        with self._task_queue.mutex:
            for task in self._task_queue.queue:
                if task.video_file == video_file:
                    return True
            # 还要检查当前正在处理的任务（即可能不在队列中，但正在被消费）
            if self._consumer_thread and self._current_processing_task and self._current_processing_task.video_file == video_file:
                return True
        return False

    def _consume_tasks(self):
        while not self._event.is_set():
            try:
                task = self._task_queue.get(timeout=1)
                if task is None:
                    continue
                self._current_processing_task = task
                logger.info(f"开始处理任务 {task.task_id}: {task.video_file}")
                task.status = TaskStatus.IN_PROGRESS
                self._tasks[task.task_id] = task
                self.save_tasks()
                task.status = self.__process_autosub(task.video_file)
                task.complete_time = datetime.now()
                self._tasks[task.task_id] = task
                self.save_tasks()
                self._task_queue.task_done()
                self._current_processing_task = None
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"消费任务时发生异常: {e}")
                logger.error(traceback.format_exc())
                self._current_processing_task = None
        logger.info("消费线程已退出")

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
        if self._asr_backend == 'openvino_genai':
            if not importlib.util.find_spec("openvino_genai"):
                logger.warn(f"openvino-genai 未安装，不进行处理")
                return False
            model_path = self.__prepare_openvino_model()
            if not model_path:
                return False
            return True

        if self._asr_backend == 'external_command':
            if not self._external_asr_command:
                logger.warn(f"外部ASR命令未配置，不进行处理")
                return False
            if '{audio}' not in self._external_asr_command and '{audio_file}' not in self._external_asr_command:
                logger.warn(f"外部ASR命令缺少音频占位符 {{audio}}，不进行处理")
                return False
            return True

        if not self._faster_whisper_model_path or not self._faster_whisper_model:
            logger.warn(f"faster-whisper配置信息不完整，不进行处理")
            return False
        if not os.path.exists(self._faster_whisper_model_path):
            logger.info(f"创建faster-whisper模型目录：{self._faster_whisper_model_path}")
            os.mkdir(self._faster_whisper_model_path)
        try:
            from faster_whisper import WhisperModel, download_model
        except ImportError:
            logger.warn(f"faster-whisper 未安装，不进行处理")
            return False
        return True

    def __process_autosub(self, video_file) -> TaskStatus:
        if not video_file:
            return TaskStatus.FAILED
        # 如果文件大小小于指定大小， 则不处理
        if os.path.getsize(video_file) < self._file_size * 1024 * 1024:
            return TaskStatus.IGNORED

        start_time = time.time()
        file_path, file_ext = os.path.splitext(video_file)
        file_name = os.path.basename(video_file)

        try:
            logger.info(f"开始处理文件：{video_file} ...")
            # 判断目的字幕（和内嵌）是否已存在
            if self.__target_subtitle_exists(video_file):
                logger.warn(f"字幕文件已经存在，不进行处理")
                return TaskStatus.IGNORED
            # 生成字幕
            ret, lang, gen_sub_path = self.__generate_subtitle(video_file, file_path, self._enable_asr)
            if not ret:
                message = f" 媒体: {file_name}\n 生成字幕失败，跳过后续处理"
                if self._send_notify:
                    self.post_message(mtype=NotificationType.Plugin, title="【自动字幕生成】", text=message)
                return TaskStatus.FAILED

            if self._translate_zh:
                # 翻译字幕
                logger.info(f"开始翻译字幕为中文 ...")
                self.__translate_zh_subtitle(lang, gen_sub_path, f"{file_path}.zh.机翻.srt")
                logger.info(f"翻译字幕完成：{file_name}.zh.机翻.srt")

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
            return TaskStatus.FAILED
        except Exception as e:
            logger.error(f"自动字幕生成 处理异常：{e}")
            end_time = time.time()
            message = f" 媒体: {file_name}\n 处理失败\n 耗时：{round(end_time - start_time, 2)}秒"
            if self._send_notify:
                self.post_message(mtype=NotificationType.Plugin, title="【自动字幕生成】", text=message)
            # 打印调用栈
            logger.error(traceback.format_exc())
            return TaskStatus.FAILED

    def __do_speech_recognition(self, audio_lang, audio_file):
        if self._asr_backend == 'openvino_genai':
            return self.__do_openvino_genai_recognition(audio_lang, audio_file)
        if self._asr_backend == 'external_command':
            return self.__do_external_speech_recognition(audio_lang, audio_file)
        return self.__do_faster_whisper_recognition(audio_lang, audio_file)

    def __do_openvino_genai_recognition(self, audio_lang, audio_file):
        lang = audio_lang or 'auto'
        try:
            model_path = self.__prepare_openvino_model()
            if not model_path:
                return False, None
            result = self.__run_openvino_genai_worker(
                audio_file=audio_file,
                model_path=model_path,
                lang=lang,
            )
            if not result.get("success"):
                logger.error(f"OpenVINO GenAI 子进程失败：{result.get('message') or result}")
                return False, None

            detected_lang = str(result.get("language") or "").strip()
            if lang == 'auto':
                lang = detected_lang or self._external_asr_default_language or 'und'

            subs = self.__openvino_payload_to_subtitles(result)
            if not subs:
                text = str(result.get("text") or "").strip()
                if text:
                    duration = max(float(result.get("duration") or 0), self.__wav_duration(audio_file), 1.0)
                    subs = [srt.Subtitle(
                        index=1,
                        start=timedelta(seconds=0),
                        end=timedelta(seconds=duration),
                        content=text,
                    )]
                else:
                    logger.info("OpenVINO GenAI 未检测到有效语音内容，生成空字幕文件以避免重复处理")

            self.__save_srt(f"{audio_file}.srt", subs)
            logger.info(f"OpenVINO GenAI 音轨转字幕完成，语言：{lang}，字幕条目：{len(subs)}")
            return True, lang
        except Exception as e:
            logger.error(f"OpenVINO GenAI 处理异常：{e}")
            logger.error(traceback.format_exc())
            return False, None

    def __do_faster_whisper_recognition(self, audio_lang, audio_file):
        """
        语音识别, 生成字幕
        :param audio_lang:
        :param audio_file:
        :return:
        """
        lang = audio_lang
        try:
            from faster_whisper import WhisperModel, download_model
            # 设置缓存目录, 防止缓存同目录出现 cross-device 错误
            cache_dir = os.path.join(self._faster_whisper_model_path, "cache")
            if not os.path.exists(cache_dir):
                os.mkdir(cache_dir)
            os.environ["HF_HUB_CACHE"] = cache_dir
            if self._huggingface_proxy:
                os.environ["HTTP_PROXY"] = settings.PROXY['http']
                os.environ["HTTPS_PROXY"] = settings.PROXY['https']
            model = WhisperModel(
                download_model(self._faster_whisper_model, local_files_only=False, cache_dir=cache_dir),
                device="cpu", compute_type="int8", cpu_threads=psutil.cpu_count(logical=False))
            
            try:
                segments, info = model.transcribe(audio_file,
                                                  language=lang if lang != 'auto' else None,
                                                  word_timestamps=True,
                                                  vad_filter=True,
                                                  temperature=0,
                                                  beam_size=5)
                logger.info("Detected language '%s' with probability %f" % (info.language, info.language_probability))

                if lang == 'auto':
                    lang = info.language
            except ValueError as e:
                if "max() iterable argument is empty" in str(e):
                    logger.info("音频文件中未检测到任何语言内容，生成空字幕文件以避免重复处理")
                    # 生成空的字幕文件，避免重复识别
                    self.__save_srt(f"{audio_file}.srt", [])
                    # 如果原本是auto检测，设置一个默认语言
                    lang = 'und' if lang == 'auto' else lang
                    return True, lang
                else:
                    raise e

            subs = []
            if lang in ['en', 'eng']:
                # 英文先生成单词级别字幕，再合并
                idx = 0
                for segment in segments:
                    if self._event.is_set():
                        logger.info(f"whisper音轨转录服务停止")
                        raise UserInterruptException(f"用户中断当前任务")
                    for word in segment.words:
                        idx += 1
                        subs.append(srt.Subtitle(index=idx,
                                                 start=timedelta(seconds=word.start),
                                                 end=timedelta(seconds=word.end),
                                                 content=word.word))
                subs = self.__merge_srt(subs)
            else:
                for i, segment in enumerate(segments):
                    if self._event.is_set():
                        logger.info(f"whisper音轨转录服务停止")
                        raise UserInterruptException(f"用户中断当前任务")
                    subs.append(srt.Subtitle(index=i,
                                             start=timedelta(seconds=segment.start),
                                             end=timedelta(seconds=segment.end),
                                             content=segment.text))
            self.__save_srt(f"{audio_file}.srt", subs)
            logger.info(f"音轨转字幕完成")
            return True, lang
        except ImportError:
            logger.warn(f"faster-whisper 未安装，不进行处理")
            return False, None
        except Exception as e:
            traceback.print_exc()
            logger.error(f"faster-whisper 处理异常：{e}")
            return False, None

    def __do_external_speech_recognition(self, audio_lang, audio_file):
        """
        调用外部 ASR 命令生成 SRT，用于接入 whisper.cpp/OpenVINO/Vulkan 等 GPU 后端。
        """
        lang = audio_lang or 'auto'
        output_prefix = f"{audio_file}.asr"
        output_srt = f"{output_prefix}.srt"
        final_srt = f"{audio_file}.srt"
        for path in [output_srt, f"{output_srt}.srt", final_srt]:
            if os.path.exists(path):
                os.remove(path)

        try:
            command = self.__format_external_asr_command(
                command=self._external_asr_command,
                audio_file=audio_file,
                output_prefix=output_prefix,
                output_srt=output_srt,
                lang=lang,
            )
            logger.info(f"开始执行外部ASR命令：{self.__clip_log(command, 800)}")
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._external_asr_timeout,
                check=False,
            )
            command_output = "\n".join(
                item for item in [completed.stdout.strip(), completed.stderr.strip()] if item
            ).strip()
            if command_output:
                logger.info(f"外部ASR输出：{self.__clip_log(command_output, 3000)}")
            if completed.returncode != 0:
                logger.error(f"外部ASR命令失败，退出码：{completed.returncode}")
                return False, None

            source_srt = self.__find_external_asr_srt(
                candidates=[output_srt, f"{output_srt}.srt", final_srt]
            )
            if not source_srt:
                logger.error(f"外部ASR命令未生成SRT文件，期望输出：{output_srt}")
                return False, None

            if source_srt != final_srt:
                SystemUtils.copy(Path(source_srt), Path(final_srt))
                try:
                    os.remove(source_srt)
                except Exception:
                    pass

            detected_lang = self.__detect_external_asr_language(command_output)
            if lang == 'auto':
                lang = detected_lang or self._external_asr_default_language or 'und'
            logger.info(f"外部ASR音轨转字幕完成，语言：{lang}")
            return True, lang
        except subprocess.TimeoutExpired:
            logger.error(f"外部ASR命令超时：{self._external_asr_timeout} 秒")
            return False, None
        except Exception as e:
            logger.error(f"外部ASR处理异常：{e}")
            logger.error(traceback.format_exc())
            return False, None

    def _save_asr_test_result(self, result: Dict[str, Any]):
        self._latest_asr_test = result
        config = self.get_config() or {}
        config['latest_asr_test'] = result
        self.update_config(config)

    def _run_asr_test(self, payload: Optional[dict] = None) -> schemas.Response:
        payload = payload or {}
        original_command = self._external_asr_command
        original_timeout = self._external_asr_timeout
        original_default_language = self._external_asr_default_language
        original_backend = self._asr_backend
        original_openvino_device = self._openvino_device
        original_openvino_model_id = self._openvino_model_id
        original_openvino_model_path = self._openvino_model_path
        original_openvino_auto_download = self._openvino_auto_download
        if payload.get('external_asr_command'):
            self._external_asr_command = str(payload.get('external_asr_command') or '').strip()
        if payload.get('external_asr_timeout'):
            self._external_asr_timeout = int(payload.get('external_asr_timeout'))
        if payload.get('external_asr_default_language'):
            self._external_asr_default_language = str(payload.get('external_asr_default_language') or '').strip()
        if payload.get('asr_backend'):
            self._asr_backend = str(payload.get('asr_backend') or '').strip()
        if payload.get('openvino_device'):
            self._openvino_device = str(payload.get('openvino_device') or '').strip().upper()
        if payload.get('openvino_model_id'):
            self._openvino_model_id = str(payload.get('openvino_model_id') or '').strip()
        if payload.get('openvino_model_path'):
            self._openvino_model_path = str(payload.get('openvino_model_path') or '').strip()
        if payload.get('openvino_auto_download') is not None:
            self._openvino_auto_download = bool(payload.get('openvino_auto_download'))

        started = time.time()
        result = {
            "tested_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "success": False,
            "message": "",
            "language": "",
            "duration": 0,
            "backend": self._asr_backend,
            "runtime": self.__probe_asr_runtime(),
        }
        try:
            if self._asr_backend == 'external_command' and not self._external_asr_command:
                result["message"] = "外部ASR命令未配置"
                return schemas.Response(success=False, message=result["message"], data=result)

            with tempfile.NamedTemporaryFile(prefix='autosub-asr-test-', suffix='.wav', delete=True) as audio_file:
                self.__write_test_wav(audio_file.name)
                ret, lang = self.__do_speech_recognition('auto', audio_file.name)
                result["language"] = lang or ""
                srt_path = f"{audio_file.name}.srt"
                result["srt_exists"] = os.path.exists(srt_path)
                if os.path.exists(srt_path):
                    with open(srt_path, 'r', encoding='utf-8', errors='ignore') as file_obj:
                        result["srt_preview"] = self.__clip_log(file_obj.read(), 1000)
                    os.remove(srt_path)
                result["success"] = bool(ret and result["srt_exists"])
                backend_name = "OpenVINO GenAI" if self._asr_backend == "openvino_genai" else "外部ASR"
                result["message"] = f"{backend_name}自检成功" if result["success"] else f"{backend_name}自检失败，未生成有效SRT"
                return schemas.Response(success=result["success"], message=result["message"], data=result)
        except Exception as err:
            result["message"] = str(err)
            return schemas.Response(success=False, message=result["message"], data=result)
        finally:
            result["duration"] = round(time.time() - started, 2)
            self._save_asr_test_result(result)
            self._external_asr_command = original_command
            self._external_asr_timeout = original_timeout
            self._external_asr_default_language = original_default_language
            self._asr_backend = original_backend
            self._openvino_device = original_openvino_device
            self._openvino_model_id = original_openvino_model_id
            self._openvino_model_path = original_openvino_model_path
            self._openvino_auto_download = original_openvino_auto_download

    @staticmethod
    def __write_test_wav(file_path: str):
        with wave.open(file_path, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b'\x00\x00' * 16000)

    def __prepare_openvino_model(self) -> Optional[Path]:
        model_path = Path(self._openvino_model_path).expanduser()
        if self.__is_openvino_model_ready(model_path):
            return model_path
        if not self._openvino_auto_download:
            logger.warn(f"OpenVINO模型目录无效且未开启自动下载：{model_path}")
            return None
        if not self._openvino_model_id:
            logger.warn("OpenVINO模型ID未配置")
            return None
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            logger.warn("huggingface_hub 未安装，无法自动下载 OpenVINO 模型")
            return None

        model_path.mkdir(parents=True, exist_ok=True)
        cache_dir = model_path.parent / "hf-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        if self._huggingface_proxy:
            os.environ["HTTP_PROXY"] = settings.PROXY['http']
            os.environ["HTTPS_PROXY"] = settings.PROXY['https']
        logger.info(f"开始下载 OpenVINO Whisper 模型：{self._openvino_model_id} -> {model_path}")
        try:
            snapshot_download(
                repo_id=self._openvino_model_id,
                local_dir=str(model_path),
                cache_dir=str(cache_dir),
                local_dir_use_symlinks=False,
            )
        except TypeError:
            snapshot_download(
                repo_id=self._openvino_model_id,
                local_dir=str(model_path),
                cache_dir=str(cache_dir),
            )
        if not self.__is_openvino_model_ready(model_path):
            logger.warn(f"OpenVINO模型下载后仍不完整：{model_path}")
            return None
        logger.info(f"OpenVINO模型已就绪：{model_path}")
        return model_path

    @staticmethod
    def __is_openvino_model_ready(model_path: Path) -> bool:
        if not model_path or not model_path.exists() or not model_path.is_dir():
            return False
        required_files = [
            "config.json",
            "openvino_encoder_model.xml",
            "openvino_decoder_model.xml",
        ]
        return all((model_path / file_name).exists() for file_name in required_files)

    @staticmethod
    def __safe_model_dir(model_id: str) -> str:
        value = str(model_id or "openvino-whisper").strip().replace("/", "__")
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value) or "openvino-whisper"

    @staticmethod
    def __read_wav_16k_mono(file_path: str) -> List[float]:
        with wave.open(file_path, 'rb') as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
        if channels != 1 or sample_width != 2 or sample_rate != 16000:
            raise ValueError(
                f"OpenVINO GenAI 需要 16kHz/16-bit/mono WAV，当前为 "
                f"{sample_rate}Hz/{sample_width * 8}bit/{channels}ch"
            )
        import array
        samples = array.array('h')
        samples.frombytes(frames)
        if samples.itemsize != 2:
            raise ValueError("当前平台不支持按 16-bit PCM 读取 WAV")
        return [max(-1.0, min(1.0, sample / 32768.0)) for sample in samples]

    @staticmethod
    def __wav_duration(file_path: str) -> float:
        with wave.open(file_path, 'rb') as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
        return frames / rate if rate else 0

    @staticmethod
    def __openvino_language_token(lang: str) -> str:
        if not lang or lang == 'auto' or lang == 'und':
            return ""
        try:
            normalized = iso639.to_iso639_1(lang) if iso639.find(lang) else lang
        except Exception:
            normalized = lang
        normalized = str(normalized or "").strip().lower()
        if not normalized:
            return ""
        return f"<|{normalized}|>"

    @staticmethod
    def __openvino_payload_to_subtitles(payload: Dict[str, Any]) -> List[srt.Subtitle]:
        chunks = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
        subtitles = []
        for index, chunk in enumerate(chunks, start=1):
            if not isinstance(chunk, dict):
                continue
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            start = float(chunk.get("start") or 0)
            end = float(chunk.get("end") or start)
            if end <= start:
                end = start + 0.1
            subtitles.append(srt.Subtitle(
                index=index,
                start=timedelta(seconds=start),
                end=timedelta(seconds=end),
                content=text,
            ))
        return subtitles

    def __run_openvino_genai_worker(self, audio_file: str, model_path: Path, lang: str) -> Dict[str, Any]:
        language_token = self.__openvino_language_token(lang)
        output_json = f"{audio_file}.openvino.json"
        if os.path.exists(output_json):
            os.remove(output_json)
        command = [
            sys.executable,
            "-c",
            self.__openvino_worker_script(),
            str(model_path),
            str(audio_file),
            str(output_json),
            str(self._openvino_device),
            str(self._openvino_max_new_tokens),
            language_token,
        ]
        started = time.time()
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self._external_asr_timeout,
            check=False,
        )
        output = "\n".join(
            item for item in [completed.stdout.strip(), completed.stderr.strip()] if item
        ).strip()
        if completed.returncode != 0:
            return {
                "success": False,
                "message": f"退出码 {completed.returncode}: {self.__clip_log(output, 3000)}",
                "duration": round(time.time() - started, 2),
            }
        if not os.path.exists(output_json):
            return {
                "success": False,
                "message": f"子进程未生成结果文件: {self.__clip_log(output, 3000)}",
                "duration": round(time.time() - started, 2),
            }
        try:
            with open(output_json, 'r', encoding='utf-8') as file_obj:
                payload = json.load(file_obj)
            payload["duration"] = round(time.time() - started, 2)
            if output and not payload.get("worker_output"):
                payload["worker_output"] = self.__clip_log(output, 3000)
            return payload
        finally:
            try:
                os.remove(output_json)
            except Exception:
                pass

    @staticmethod
    def __openvino_worker_script() -> str:
        return r'''
import array
import json
import sys
import traceback
import wave


def read_wav_16k_mono(file_path):
    with wave.open(file_path, "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    if channels != 1 or sample_width != 2 or sample_rate != 16000:
        raise ValueError(
            f"need 16kHz/16-bit/mono WAV, got {sample_rate}Hz/{sample_width * 8}bit/{channels}ch"
        )
    samples = array.array("h")
    samples.frombytes(frames)
    return [max(-1.0, min(1.0, sample / 32768.0)) for sample in samples], len(samples) / sample_rate


def result_text(result):
    for attr in ("text", "m_text"):
        value = getattr(result, attr, None)
        if value:
            return str(value)
    texts = getattr(result, "texts", None)
    if isinstance(texts, list) and texts:
        return str(texts[0])
    return str(result or "")


model_path, audio_file, output_json, device, max_new_tokens, language_token = sys.argv[1:7]
payload = {"success": False, "message": "", "language": "", "text": "", "chunks": []}
try:
    import openvino_genai as ov_genai

    raw_speech, duration = read_wav_16k_mono(audio_file)
    pipe = ov_genai.WhisperPipeline(model_path, device)
    kwargs = {"return_timestamps": True, "max_new_tokens": int(max_new_tokens or 448)}
    if language_token:
        kwargs["language"] = language_token
    result = pipe.generate(raw_speech, **kwargs)
    chunks = []
    for chunk in (getattr(result, "chunks", None) or []):
        text = str(getattr(chunk, "text", "") or "").strip()
        if not text:
            continue
        chunks.append({
            "start": float(getattr(chunk, "start_ts", 0) or 0),
            "end": float(getattr(chunk, "end_ts", 0) or 0),
            "text": text,
        })
    payload.update({
        "success": True,
        "message": "ok",
        "language": str(getattr(result, "language", "") or ""),
        "text": result_text(result),
        "chunks": chunks,
        "duration": duration,
    })
except Exception as err:
    payload.update({"success": False, "message": f"{type(err).__name__}: {err}", "traceback": traceback.format_exc()})
with open(output_json, "w", encoding="utf-8") as file_obj:
    json.dump(payload, file_obj, ensure_ascii=False)
if not payload["success"]:
    sys.exit(1)
'''

    @staticmethod
    def __probe_asr_runtime() -> Dict[str, Any]:
        openvino_status = "installed" if importlib.util.find_spec("openvino_genai") else "missing"
        hf_status = "installed" if importlib.util.find_spec("huggingface_hub") else "missing"
        package_versions = AutoSubv2Gpu.__probe_python_package_versions_safe()
        devices = AutoSubv2Gpu.__probe_openvino_devices_safe()
        command = (
            "printf 'bins='; "
            "for b in whisper-cli whisper.cpp main whisper; do command -v \"$b\" 2>/dev/null | head -n 1; done; "
            "printf '\\nmodels='; "
            "find /models /config /moviepilot /app -maxdepth 4 -type f "
            "\\( -name 'ggml*.bin' -o -name '*whisper*.bin' -o -name '*.onnx' -o -name '*.xml' \\) "
            "2>/dev/null | head -n 20"
        )
        try:
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            return {
                "returncode": completed.returncode,
                "openvino_genai": openvino_status,
                "huggingface_hub": hf_status,
                "package_versions": package_versions,
                "openvino_devices": devices,
                "output": AutoSubv2Gpu.__clip_log(
                    "\n".join(item for item in [completed.stdout.strip(), completed.stderr.strip()] if item),
                    3000,
                ),
            }
        except Exception as err:
            return {
                "returncode": -1,
                "openvino_genai": openvino_status,
                "huggingface_hub": hf_status,
                "package_versions": package_versions,
                "openvino_devices": devices,
                "output": str(err),
            }

    @staticmethod
    def __probe_openvino_devices_safe() -> List[str]:
        script = (
            "import json\n"
            "try:\n"
            "    import openvino as ov\n"
            "    print(json.dumps([str(x) for x in ov.Core().available_devices], ensure_ascii=False))\n"
            "except Exception as err:\n"
            "    print(json.dumps([f'openvino_device_probe_error: {type(err).__name__}: {err}'], ensure_ascii=False))\n"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if completed.returncode != 0:
                output = "\n".join(
                    item for item in [completed.stdout.strip(), completed.stderr.strip()] if item
                ).strip()
                return [f"openvino_device_probe_exit_{completed.returncode}: {AutoSubv2Gpu.__clip_log(output, 1000)}"]
            return json.loads(completed.stdout.strip() or "[]")
        except Exception as err:
            return [f"openvino_device_probe_error: {type(err).__name__}: {err}"]

    @staticmethod
    def __probe_python_package_versions_safe() -> Dict[str, str]:
        packages = ["openvino", "openvino-genai", "openvino-tokenizers", "huggingface-hub"]
        versions = {}
        for package in packages:
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "missing"
            except Exception as err:
                versions[package] = f"error: {type(err).__name__}: {err}"
        return versions

    @staticmethod
    def __format_external_asr_command(command: str, audio_file: str, output_prefix: str,
                                      output_srt: str, lang: str) -> str:
        language = lang if lang and lang != 'auto' else 'auto'
        values = {
            'audio': audio_file,
            'audio_file': audio_file,
            'output': output_prefix,
            'output_srt': output_srt,
            'language': language,
            'lang': language,
        }
        safe_values = {key: shlex.quote(str(value)) for key, value in values.items()}
        return command.format_map(_SafeFormatDict(safe_values))

    @staticmethod
    def __find_external_asr_srt(candidates: List[str]) -> str:
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return ""

    @staticmethod
    def __detect_external_asr_language(output: str) -> str:
        if not output:
            return ""
        patterns = [
            r"auto[- ]detected language\s*[:：]\s*([a-zA-Z_-]{2,12})",
            r"detected language\s*['\"]?([a-zA-Z_-]{2,12})['\"]?",
            r"language\s*[:=]\s*([a-zA-Z_-]{2,12})",
        ]
        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                value = match.group(1).strip().replace("_", "-")
                if value.lower() not in {"with", "probability", "auto"}:
                    return value
        return ""

    @staticmethod
    def __clip_log(text: Any, limit: int) -> str:
        value = "" if text is None else str(text)
        if len(value) <= limit:
            return value
        return value[:limit] + "...(已截断)"

    def __generate_subtitle(self, video_file, subtitle_file, enable_asr=True):
        """
        生成字幕
        :param video_file: 视频文件
        :param subtitle_file: 字幕文件, 不包含后缀
        :return: 生成成功返回True，字幕语言,字幕路径，否则返回False, None, None
        """
        # 获取文件元数据
        video_meta = Ffmpeg().get_video_metadata(video_file)
        if not video_meta:
            logger.error(f"获取视频文件元数据失败，跳过后续处理")
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
            return False, None, None
        
        # 如果开启了自动语言检测，直接设置为auto，跳过metadata的语言信息
        if self._auto_detect_language:
            logger.info("已开启自动语言检测，将使用whisper模型自动识别语言")
            audio_lang = 'auto'
        elif not iso639.find(audio_lang) or not iso639.to_iso639_1(audio_lang):
            logger.info(f"字幕源偏好：{self._translate_preference} 未从音轨元数据中获取到语言信息")
            audio_lang = 'auto'

        # 当字幕源偏好为origin_first时，优先使用音轨语言
        if self._translate_preference == "origin_first":
            prefer_subtitle_langs = ['en', 'eng'] if audio_lang == 'auto' else [audio_lang,
                                                                                iso639.to_iso639_1(audio_lang)]
        # 获取外挂字幕
        logger.info(f"使用 {prefer_subtitle_langs} 匹配已有外挂字幕文件 ...")
        external_sub_exist, external_sub_lang, exist_sub_name = self.__external_subtitle_exists(video_file,
                                                                                                prefer_subtitle_langs,
                                                                                                only_srt=True,
                                                                                                strict=strict)
        # 获取内嵌字幕
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
                return True, iso639.to_iso639_1(external_sub_lang), get_sub_path()
            elif inner_sub_exist:
                logger.info(f"字幕源偏好：{self._translate_preference} 内嵌字幕存在，字幕语言 {inner_sub_lang}")
                extract_subtitle = True
            else:
                logger.info(f"字幕源偏好：{self._translate_preference} 未匹配到外挂或内嵌字幕,需要使用asr提取")
        else:  # english_first/origin_first
            if external_sub_exist and external_sub_lang in prefer_subtitle_langs:
                logger.info(f"字幕源偏好：{self._translate_preference} 外挂字幕存在，字幕语言 {external_sub_lang}")
                return True, iso639.to_iso639_1(external_sub_lang), get_sub_path()
            elif inner_sub_exist and inner_sub_lang in prefer_subtitle_langs:
                logger.info(f"字幕源偏好：{self._translate_preference} 内嵌字幕存在，字幕语言 {inner_sub_lang}")
                extract_subtitle = True
            elif external_sub_exist:
                logger.info(f"字幕源偏好：{self._translate_preference} 外挂字幕存在，字幕语言 {external_sub_lang}")
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
            Ffmpeg().extract_subtitle_from_video(video_file, extracted_sub_path, subtitle_index)
            logger.info(f"提取字幕完成：{extracted_sub_path}")
            return True, inner_sub_lang, extracted_sub_path
        # 使用asr音轨识别字幕
        if audio_lang != 'auto':
            audio_lang = iso639.to_iso639_1(audio_lang)

        if not enable_asr:
            logger.info(f"未开启语音识别，且无已有字幕文件，跳过后续处理")
            return False, None, None

        # 清理异常退出的临时文件
        tempdir = tempfile.gettempdir()
        for file in os.listdir(tempdir):
            if file.startswith('autosub-'):
                os.remove(os.path.join(tempdir, file))

        with tempfile.NamedTemporaryFile(prefix='autosub-', suffix='.wav', delete=True) as audio_file:
            # 提取音频
            logger.info(f"正在提取音频：{audio_file.name} ...")
            Ffmpeg().extract_wav_from_video(video_file, audio_file.name, audio_index)
            logger.info(f"提取音频完成：{audio_file.name}")

            # 生成字幕
            logger.info(f"开始生成字幕, 语言 {audio_lang} ...")
            ret, lang = self.__do_speech_recognition(audio_lang, audio_file.name)
            if ret:
                logger.info(f"生成字幕成功，原始语言：{lang}")
                # 复制字幕文件
                SystemUtils.copy(Path(f"{audio_file.name}.srt"), Path(f"{subtitle_file}.{lang}.srt"))
                logger.info(f"复制字幕文件：{subtitle_file}.{lang}.srt")
                # 删除临时文件
                os.remove(f"{audio_file.name}.srt")
                return ret, lang, Path(f"{subtitle_file}.{lang}.srt")
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
            if not audio_index:
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

    def __process_items(self, all_subs: list, items: list) -> list:
        """统一处理入口（支持批量和单条）"""
        if self._enable_batch and len(items) > 1:
            return self.__process_batch(all_subs, items)
        return [self.__process_single(all_subs, item) for item in items]

    def __translate_to_zh(self, text: str, context: str = None) -> str:
        if self._event.is_set():
            raise UserInterruptException("用户中断当前任务")
        return self._openai.translate_to_zh(text, context, max_retries=self._max_retries)

    def __process_batch(self, all_subs: list, batch: list) -> list:
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
            self._stats['batch_success'] += len(batch)
            return batch
        except Exception as e:
            logger.warning(f"批次翻译失败（{str(e)}），降级到单行匹配...")
            self._stats['batch_fail'] += 1
            return [self.__process_single(all_subs, item) for item in batch]

    def __process_single(self, all_subs: List[srt.Subtitle], item: srt.Subtitle) -> srt.Subtitle:
        """单条处理逻辑"""
        idx = all_subs.index(item)
        context = self.__get_context(all_subs, [idx], is_batch=False) if self._context_window > 0 else None
        success, trans = self.__translate_to_zh(item.content, context)

        if success:
            item.content = f"{trans}\n{item.content}"
            self._stats['line_fallback'] += 1
            return item
        else:
            item.content = f"[翻译失败]\n{item.content}"
            return item

    def __translate_zh_subtitle(self, source_lang: str, source_subtitle: str, dest_subtitle: str):
        self._stats = {'total': 0, 'batch_success': 0, 'batch_fail': 0, 'line_fallback': 0}
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
            return
            
        self._stats['total'] = len(valid_subs)
        processed = []
        current_batch = []

        for item in valid_subs:
            current_batch.append(item)

            if len(current_batch) >= self._batch_size:
                processed += self.__process_items(valid_subs, current_batch)
                current_batch = []
                logger.info(f"进度: {len(processed)}/{len(valid_subs)}")

        if current_batch:
            processed += self.__process_items(valid_subs, current_batch)

        self.__save_srt(dest_subtitle, processed)
        
        success_rate = (self._stats['batch_success'] / self._stats['total'] * 100) if self._stats['total'] > 0 else 0.0
        
        logger.info(f"""
    翻译完成！
    总处理条目: {self._stats['total']}
    批次成功: {self._stats['batch_success']} ({success_rate:.1f}%)
    批次失败: {self._stats['batch_fail']}
    行补偿翻译: {self._stats['line_fallback']}
            """)

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
        if ret and subtitle_lang in prefer_langs:
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
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {'v-show': 'run_now'},
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 12},
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'path_list',
                                            'label': '媒体路径',
                                            'rows': 3,
                                            'placeholder': '绝对路径，每行一个。支持文件和文件夹'
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
                                            'model': 'file_size',
                                            'label': '触发字幕生成的视频文件不小于(MB)',
                                            'placeholder': '默认10'
                                        }
                                    }
                                ]
                            },
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
                                            'hint': '使用whisper模型自动检测语言，而非依赖视频元数据'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4, 'v-show': 'enable_asr'},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'asr_backend',
                                            'label': 'ASR 后端',
                                            'items': [
                                                {'title': 'OpenVINO GenAI（Intel GPU）',
                                                 'value': 'openvino_genai'},
                                                {'title': '外部命令（GPU/whisper.cpp/OpenVINO）',
                                                 'value': 'external_command'},
                                                {'title': 'faster-whisper（CPU兼容）',
                                                 'value': 'faster_whisper'}
                                            ]
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
                                'props': {'cols': 12, 'md': 5},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'openvino_model_id',
                                            'label': 'OpenVINO模型ID',
                                            'placeholder': 'OpenVINO/whisper-base-int8-ov',
                                            'hint': '默认使用 HuggingFace 上已转换的 OpenVINO Whisper INT8 模型',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'openvino_device',
                                            'label': 'OpenVINO设备',
                                            'items': ['GPU', 'CPU', 'AUTO']
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 2},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'openvino_auto_download',
                                            'label': '自动下载模型'
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
                                            'model': 'openvino_max_new_tokens',
                                            'label': '最大Token',
                                            'placeholder': '448',
                                            'type': 'number'
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
                                'props': {'cols': 12, 'md': 12},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'openvino_model_path',
                                            'label': 'OpenVINO模型目录',
                                            'placeholder': '留空时使用插件数据目录/openvino-models/OpenVINO__whisper-base-int8-ov'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {'v-show': "enable_asr && asr_backend == 'external_command'"},
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 12},
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'external_asr_command',
                                            'label': '外部ASR命令',
                                            'rows': 3,
                                            'auto-grow': True,
                                            'placeholder': 'whisper-cli -m /models/ggml-base.bin -f {audio} -l {language} -osrt -of {output}',
                                            'hint': '占位符：{audio} 输入音频，{language} 语言，{output} 输出前缀，{output_srt} 完整SRT路径。占位符会自动加 shell 引号。',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {'v-show': "enable_asr && asr_backend == 'external_command'"},
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'external_asr_timeout',
                                            'label': '外部ASR超时(秒)',
                                            'placeholder': '7200',
                                            'type': 'number'
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
                                            'model': 'external_asr_default_language',
                                            'label': '自动检测失败默认语言',
                                            'placeholder': 'en'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'faster_whisper_model',
                                            'label': 'faster-whisper模型选择（兼容后端）',
                                            'items': ['tiny', 'base', 'small', 'medium',
                                                      'large-v3',
                                                      {'title': 'large-v3-turbo',
                                                       'value': 'deepdml/faster-whisper-large-v3-turbo-ct2'},
                                                      ]
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
                                'props': {'cols': 12, 'md': 12, 'v-show': 'enable_asr'},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'proxy',
                                            'hint': '需配置MP环境变量PROXY_HOST',
                                            'label': '下载模型使用代理'
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
                                'props': {'v-show': 'translate_zh'},
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
                                                                    'model': 'use_chatgpt',
                                                                    'label': '复用ChatGPT插件配置'
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'use_chatgpt_trigger',
                                                            'class': 'd-none',
                                                            'text': 'trigger',
                                                            'change': 'use_chatgpt_trigger = use_chatgpt ? 1 : 0'
                                                        }
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
                                                                    'v-show': '!use_chatgpt',
                                                                    'v-if': '!use_chatgpt'
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
                                                                    'v-show': '!use_chatgpt'
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
                                                                    'v-show': '!use_chatgpt'
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
                                                                    'v-show': '!use_chatgpt'
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
                                                                    'label': '自定义模型',
                                                                    'placeholder': 'gpt-3.5-turbo',
                                                                    'v-show': '!use_chatgpt'
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
                                                    'href': 'https://github.com/EllickWANG/moviepilot-plugins/blob/main/plugins.v2/autosubv2gpu/README.md',
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
            "file_size": "10",
            "translate_preference": "english_first",
            "translate_zh": False,
            "enable_asr": True,
            "asr_backend": "openvino_genai",
            "auto_detect_language": False,
            "external_asr_command": "",
            "external_asr_timeout": 7200,
            "external_asr_default_language": "en",
            "openvino_model_id": "OpenVINO/whisper-base-int8-ov",
            "openvino_model_path": "",
            "openvino_device": "GPU",
            "openvino_auto_download": True,
            "openvino_max_new_tokens": 448,
            "latest_asr_test": {},
            "faster_whisper_model": "base",
            "proxy": True,
            "use_chatgpt": True,
            "use_chatgpt_trigger": 0,
            "openai_proxy": False,
            "compatible": False,
            "openai_url": "https://api.openai.com",
            "openai_key": None,
            "openai_model": "gpt-3.5-turbo",
            "context_window": 5,
            "max_retries": 3,
            "enable_merge": False,
            "enable_batch": True,
            "batch_size": 10,
        }

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/asr/test",
                "endpoint": _api_test_asr,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "测试外部ASR命令",
                "description": "生成临时静音 wav，调用外部 ASR 命令，验证命令、模型和 SRT 输出是否可用。",
            },
        ]

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
            TaskStatus.COMPLETED: "text-success",
            TaskStatus.IGNORED: "text-muted",
            TaskStatus.FAILED: "text-error"
        }

        rows = []
        for task_id, task in sorted_tasks:
            source_label = {
                TaskSource.MANUAL: "手动添加",
                TaskSource.EVENT: "入库触发"
            }.get(task.source, task.source)

            status_text = {
                TaskStatus.PENDING: "等待中",
                TaskStatus.IN_PROGRESS: "处理中",
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
        if self._running:
            self._event.set()
        if self._consumer_thread and self._consumer_thread.is_alive():
            logger.info("正在停止当前任务...")
            # self._consumer_thread.join(timeout=3)
            self._consumer_thread.join()

        if self._task_queue:
            while not self._task_queue.empty():
                self._task_queue.get_nowait()
                self._task_queue.task_done()
            logger.info("任务队列已清空")
        if self._tasks is not None:
            for task_id in list(self._tasks.keys()):
                task = self._tasks[task_id]
                if task.status == TaskStatus.PENDING or task.status == TaskStatus.IN_PROGRESS:
                    task.status = TaskStatus.FAILED
                    task.complete_time = datetime.now()
            self.save_tasks()  # 持久化更新后的任务列表
        self._running = False
        self._event.clear()
        logger.info(f"自动字幕生成服务已停止")


def _api_test_asr(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = AutoSubv2Gpu._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    return plugin._run_asr_test(payload)
