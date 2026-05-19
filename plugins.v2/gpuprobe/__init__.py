from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Tuple

from app import schemas
from app.log import logger
from app.plugins import _PluginBase


class GpuProbe(_PluginBase):
    plugin_name = "GPU 探测"
    plugin_desc = "检测容器内 Intel GPU、OpenVINO、Vulkan 与 VAAPI 是否可用。"
    plugin_icon = "mdi-chip"
    plugin_version = "1.0.0"
    plugin_author = "Ellick"
    plugin_order = 95
    auth_level = 1

    _instance: Optional["GpuProbe"] = None
    _enabled = True
    _timeout = 20
    _openvino_probe = True
    _command_probe = True
    _latest_result: Dict[str, Any] = {}
    _job_state: Dict[str, Any] = {}
    _lock = Lock()

    def init_plugin(self, config: dict = None):
        config = config or {}
        self.__class__._instance = self
        self._enabled = _to_bool(config.get("enabled", True), True)
        self._timeout = _int_or_default(config.get("timeout"), 20, 5, 120)
        self._openvino_probe = _to_bool(config.get("openvino_probe", True), True)
        self._command_probe = _to_bool(config.get("command_probe", True), True)
        self._latest_result = config.get("latest_result") if isinstance(config.get("latest_result"), dict) else {}
        self._job_state = config.get("job_state") if isinstance(config.get("job_state"), dict) else {}

        self.__class__._enabled = self._enabled
        self.__class__._timeout = self._timeout
        self.__class__._openvino_probe = self._openvino_probe
        self.__class__._command_probe = self._command_probe
        self.__class__._latest_result = self._latest_result
        self.__class__._job_state = self._job_state

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[dict]:
        return []

    def get_api(self) -> List[dict]:
        return [
            {
                "path": "/run",
                "endpoint": _api_run_probe,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "运行GPU探测",
                "description": "后台检测容器内 GPU 映射、驱动工具和 OpenVINO GPU 推理是否可用。",
            },
            {
                "path": "/clear",
                "endpoint": _api_clear_probe,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "清空GPU探测结果",
                "description": "清空最近一次 GPU 探测结果。",
            },
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], dict]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            _col(12, 4, {
                                "component": "VSwitch",
                                "props": {"model": "enabled", "label": "启用插件"},
                            }),
                            _col(12, 4, {
                                "component": "VTextField",
                                "props": {
                                    "model": "timeout",
                                    "label": "单项超时(秒)",
                                    "type": "number",
                                    "min": 5,
                                    "max": 120,
                                },
                            }),
                            _col(12, 4, {
                                "component": "VSwitch",
                                "props": {"model": "openvino_probe", "label": "检测 OpenVINO"},
                            }),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            _col(12, 4, {
                                "component": "VSwitch",
                                "props": {"model": "command_probe", "label": "检测系统工具"},
                            }),
                            _col(12, 8, {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "text": "探测只读取 /dev/dri 并运行最小检测命令，不会处理媒体文件，也不会改动系统配置。",
                                },
                            }),
                        ],
                    },
                ],
            }
        ], {
            "enabled": True,
            "timeout": 20,
            "openvino_probe": True,
            "command_probe": True,
            "latest_result": {},
            "job_state": {},
        }

    def get_page(self) -> Optional[List[dict]]:
        return _probe_page(self)

    def stop_service(self):
        pass

    def _config_payload(self, **overrides) -> Dict[str, Any]:
        payload = {
            "enabled": self._enabled,
            "timeout": self._timeout,
            "openvino_probe": self._openvino_probe,
            "command_probe": self._command_probe,
            "latest_result": self._latest_result,
            "job_state": self._job_state,
        }
        payload.update(overrides)
        return payload

    def _save_result(self, result: Dict[str, Any]):
        self._latest_result = result
        self.__class__._latest_result = result
        self.update_config(self._config_payload(latest_result=result))

    def _set_job_state(self, status: str, message: str = "",
                       started_at: Optional[str] = None, finished_at: Optional[str] = None):
        with self.__class__._lock:
            state = dict(self._job_state or {})
            state.update({
                "status": status,
                "message": message,
            })
            if started_at is not None:
                state["started_at"] = started_at
            if finished_at is not None:
                state["finished_at"] = finished_at
            self._job_state = state
            self.__class__._job_state = state
        self.update_config(self._config_payload(job_state=state))

    def _clear(self):
        self._latest_result = {}
        self._job_state = {}
        self.__class__._latest_result = {}
        self.__class__._job_state = {}
        self.update_config(self._config_payload(latest_result={}, job_state={}))

    def _start_probe(self) -> schemas.Response:
        if not self._enabled:
            return schemas.Response(success=False, message="插件未启用")

        with self.__class__._lock:
            state = dict(self._job_state or {})
            if state.get("status") == "running":
                return schemas.Response(
                    success=True,
                    message=f"GPU 探测已在后台运行，开始时间：{state.get('started_at') or '-'}",
                    data=state,
                )

        self._set_job_state("running", "后台运行中", started_at=_now(), finished_at="")

        def runner():
            try:
                result = _build_probe_result(
                    timeout=self._timeout,
                    openvino_probe=self._openvino_probe,
                    command_probe=self._command_probe,
                )
                self._save_result(result)
                summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
                status = str(summary.get("status") or "success")
                message = str(summary.get("message") or "探测完成")
                self._set_job_state(status, message, finished_at=_now())
                logger.info(f"GPU 探测完成：{message}")
            except Exception as err:
                logger.error(f"GPU 探测失败：{err}", exc_info=True)
                result = {
                    "checked_at": _now(),
                    "summary": {"status": "error", "message": str(err)},
                    "checks": [_check("探测任务", "error", str(err))],
                }
                self._save_result(result)
                self._set_job_state("error", str(err), finished_at=_now())

        Thread(target=runner, name="gpuprobe-run", daemon=True).start()
        return schemas.Response(
            success=True,
            message="GPU 探测已在后台运行，完成后刷新页面查看结果",
            data=self._job_state,
        )


def _api_run_probe() -> schemas.Response:
    plugin = GpuProbe._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    return plugin._start_probe()


def _api_clear_probe() -> schemas.Response:
    plugin = GpuProbe._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    plugin._clear()
    return schemas.Response(success=True, message="GPU 探测结果已清空")


def _build_probe_result(timeout: int, openvino_probe: bool, command_probe: bool) -> Dict[str, Any]:
    start = time.time()
    checks: List[Dict[str, Any]] = []
    render_nodes = _render_nodes()

    checks.append(_probe_dri(render_nodes))
    for node in render_nodes[:4]:
        checks.append(_probe_render_node(node))

    checks.append(_check(
        "Python 环境",
        "success",
        f"{sys.executable} · Python {platform.python_version()} · {platform.platform()}",
    ))

    if openvino_probe:
        openvino_devices = _probe_openvino_devices(timeout)
        checks.append(openvino_devices)
        if openvino_devices.get("state") == "success" and _detail_has_gpu(openvino_devices.get("detail")):
            checks.append(_probe_openvino_gpu_infer(timeout))
        else:
            checks.append(_check(
                "OpenVINO GPU 最小推理",
                "warning",
                "OpenVINO 未发现 GPU 设备，跳过 GPU 编译和推理。",
            ))
    else:
        checks.append(_check("OpenVINO", "skipped", "配置中已关闭 OpenVINO 检测。"))

    if command_probe:
        checks.extend(_probe_system_commands(timeout, render_nodes[0] if render_nodes else ""))
    else:
        checks.append(_check("系统工具", "skipped", "配置中已关闭系统工具检测。"))

    summary = _summarize_checks(checks)
    return {
        "checked_at": _now(),
        "duration": round(time.time() - start, 2),
        "summary": summary,
        "checks": checks,
    }


def _probe_dri(render_nodes: List[str]) -> Dict[str, Any]:
    dri = Path("/dev/dri")
    if not dri.exists():
        return _check("容器设备 /dev/dri", "error", "容器内不存在 /dev/dri，GPU 设备还没有映射进容器。")
    try:
        names = sorted(item.name for item in dri.iterdir())
    except Exception as err:
        return _check("容器设备 /dev/dri", "error", f"无法读取 /dev/dri：{err}")
    state = "success" if render_nodes else "warning"
    detail = f"发现：{', '.join(names) if names else '空目录'}"
    if not render_nodes:
        detail += "；没有 renderD* 节点，OpenVINO/Vulkan 通常需要 render 节点。"
    return _check("容器设备 /dev/dri", state, detail)


def _probe_render_node(node: str) -> Dict[str, Any]:
    path = Path(node)
    try:
        stat = path.stat()
        readable = os.access(path, os.R_OK)
        writable = os.access(path, os.W_OK)
        state = "success" if readable and writable else "error"
        detail = (
            f"{node} mode={oct(stat.st_mode & 0o777)} owner={stat.st_uid}:{stat.st_gid} "
            f"read={'yes' if readable else 'no'} write={'yes' if writable else 'no'}"
        )
        if not readable or not writable:
            detail += "；当前容器用户没有完整读写权限。"
        return _check("render 节点权限", state, detail)
    except Exception as err:
        return _check("render 节点权限", "error", f"{node} 读取失败：{err}")


def _probe_openvino_devices(timeout: int) -> Dict[str, Any]:
    script = r"""
try:
    import openvino as ov
    Core = getattr(ov, "Core", None)
    if Core is None:
        from openvino.runtime import Core
except Exception as exc:
    print(f"import_error={type(exc).__name__}: {exc}")
    raise

core = Core()
devices = [str(item) for item in core.available_devices]
print("available_devices=" + ",".join(devices))
for device in devices:
    try:
        print(f"{device}.FULL_DEVICE_NAME=" + str(core.get_property(device, "FULL_DEVICE_NAME")))
    except Exception as exc:
        print(f"{device}.FULL_DEVICE_NAME_ERROR={type(exc).__name__}: {exc}")
"""
    check = _run_python("OpenVINO 可用设备", script, timeout)
    if check.get("state") == "error" and _looks_like_missing_openvino(check.get("detail")):
        check["state"] = "warning"
        check["detail"] = "当前 MoviePilot Python 环境未安装 OpenVINO。"
    return check


def _probe_openvino_gpu_infer(timeout: int) -> Dict[str, Any]:
    script = r"""
import numpy as np

try:
    import openvino as ov
    Core = getattr(ov, "Core", None)
    Model = getattr(ov, "Model", None)
    opset8 = getattr(ov, "opset8", None)
    if Core is None or Model is None or opset8 is None:
        from openvino.runtime import Core, Model, opset8
except Exception as exc:
    print(f"import_error={type(exc).__name__}: {exc}")
    raise

core = Core()
param = opset8.parameter([1], dtype=np.float32, name="x")
one = opset8.constant(np.array([1.0], dtype=np.float32))
result = opset8.add(param, one)
model = Model([result], [param], "moviepilot_gpu_probe")
compiled = core.compile_model(model, "GPU")
request = compiled.create_infer_request()
outputs = request.infer({compiled.input(0): np.array([41.0], dtype=np.float32)})
values = []
for value in outputs.values():
    try:
        values.append(value.tolist())
    except AttributeError:
        values.append(str(value))
print("gpu_infer_result=" + str(values))
"""
    return _run_python("OpenVINO GPU 最小推理", script, timeout)


def _probe_system_commands(timeout: int, render_node: str) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    checks.append(_run_optional_command("Vulkan 驱动", "vulkaninfo", ["--summary"], timeout))
    checks.append(_run_optional_command(
        "VAAPI 设备",
        "vainfo",
        ["--display", "drm", "--device", render_node] if render_node else [],
        timeout,
    ))
    checks.append(_run_optional_command("OpenCL 设备", "clinfo", ["-l"], timeout))
    checks.append(_run_optional_command("Intel GPU 工具", "intel_gpu_top", ["-L"], timeout))
    checks.append(_run_optional_command("FFmpeg 硬件加速列表", "ffmpeg", ["-hide_banner", "-hwaccels"], timeout))
    if render_node:
        checks.append(_run_optional_command(
            "FFmpeg VAAPI 初始化",
            "ffmpeg",
            [
                "-hide_banner", "-v", "error",
                "-init_hw_device", f"vaapi=va:{render_node}",
                "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.1",
                "-frames:v", "1",
                "-f", "null", "-",
            ],
            timeout,
        ))
    return checks


def _run_python(name: str, script: str, timeout: int) -> Dict[str, Any]:
    python_bin = sys.executable or shutil.which("python3") or "python3"
    return _run_command(name, [python_bin, "-c", script], timeout)


def _run_optional_command(name: str, binary: str, args: List[str], timeout: int) -> Dict[str, Any]:
    path = shutil.which(binary)
    if not path:
        return _check(name, "warning", f"容器内未安装 {binary}。")
    return _run_command(name, [path, *args], timeout)


def _run_command(name: str, args: List[str], timeout: int) -> Dict[str, Any]:
    start = time.time()
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _check(name, "error", f"命令超时：{' '.join(args)}", time.time() - start)
    except Exception as err:
        return _check(name, "error", f"命令执行失败：{err}", time.time() - start)

    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
    detail = output or f"命令退出码：{completed.returncode}"
    state = "success" if completed.returncode == 0 else "error"
    return _check(name, state, detail, time.time() - start)


def _summarize_checks(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    openvino_ok = _has_success(checks, "OpenVINO GPU 最小推理")
    vulkan_ok = _has_success(checks, "Vulkan 驱动")
    vaapi_ok = _has_success(checks, "VAAPI 设备") or _has_success(checks, "FFmpeg VAAPI 初始化")
    render_ok = _has_success(checks, "render 节点权限")
    dri_ok = _has_success(checks, "容器设备 /dev/dri")
    error_count = len([item for item in checks if item.get("state") == "error"])
    warning_count = len([item for item in checks if item.get("state") == "warning"])

    if openvino_ok:
        status = "success"
        message = "OpenVINO 已能在 GPU 上完成最小推理，容器可以调用 Intel GPU。"
    elif vulkan_ok:
        status = "warning"
        message = "Vulkan 驱动可用，容器能看到可用 GPU 后端；还未验证 ASR 模型推理。"
    elif vaapi_ok:
        status = "warning"
        message = "VAAPI/FFmpeg 可以访问 GPU，适合视频硬件能力；ASR 还需要 OpenVINO 或 whisper.cpp Vulkan 后端。"
    elif render_ok or dri_ok:
        status = "warning"
        message = "/dev/dri 已映射，但当前镜像缺少或无法调用可用的 GPU 推理后端。"
    else:
        status = "error"
        message = "容器内没有可用 GPU 设备节点，请先确认 Docker 是否映射 /dev/dri。"

    return {
        "status": status,
        "message": message,
        "errors": error_count,
        "warnings": warning_count,
    }


def _probe_page(plugin: GpuProbe) -> List[dict]:
    result = plugin._latest_result if isinstance(plugin._latest_result, dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    checks = result.get("checks") if isinstance(result.get("checks"), list) else []
    job = plugin._job_state if isinstance(plugin._job_state, dict) else {}
    status = str(summary.get("status") or job.get("status") or "idle")
    message = str(summary.get("message") or job.get("message") or "还没有运行探测")
    checked_at = str(result.get("checked_at") or "-")
    return [
        {
            "component": "VCard",
            "props": {"variant": "outlined", "class": "mb-3"},
            "content": [
                {
                    "component": "VCardText",
                    "content": [
                        {
                            "component": "div",
                            "props": {"class": "d-flex flex-wrap align-center justify-space-between ga-3 mb-2"},
                            "content": [
                                {
                                    "component": "div",
                                    "content": [
                                        {"component": "div", "props": {"class": "text-h6"}, "text": "GPU 探测"},
                                        {
                                            "component": "div",
                                            "props": {"class": "text-caption text-medium-emphasis"},
                                            "text": f"版本 {plugin.plugin_version} · 最近检测 {checked_at}",
                                        },
                                    ],
                                },
                                _status_chip(_state_text(status), _state_color(status)),
                            ],
                        },
                        {
                            "component": "VAlert",
                            "props": {
                                "type": _alert_type(status),
                                "variant": "tonal",
                                "text": message,
                            },
                        },
                        {
                            "component": "VRow",
                            "props": {"dense": True, "class": "mt-2"},
                            "content": [
                                _metric_col("错误", summary.get("errors", 0), "error"),
                                _metric_col("警告", summary.get("warnings", 0), "warning"),
                                _metric_col("耗时", f"{result.get('duration', '-') } 秒", "duration"),
                                _metric_col("任务", _state_text(str(job.get("status") or "idle")), job.get("finished_at") or job.get("started_at") or "-"),
                            ],
                        },
                    ],
                },
            ],
        },
        {
            "component": "VCard",
            "props": {"variant": "outlined", "class": "mb-3"},
            "content": [
                {
                    "component": "VCardText",
                    "content": [
                        {
                            "component": "div",
                            "props": {"class": "d-flex flex-wrap ga-2"},
                            "content": [
                                _action_button("运行探测", "mdi-play-circle", "primary", "plugin/GpuProbe/run"),
                                _action_button("清空结果", "mdi-delete-outline", "secondary", "plugin/GpuProbe/clear"),
                            ],
                        },
                        {
                            "component": "div",
                            "props": {"class": "text-caption text-medium-emphasis mt-2"},
                            "text": "点击后后台运行，完成后刷新页面查看最新结果。",
                        },
                    ],
                }
            ],
        },
        _checks_table(checks),
    ]


def _checks_table(checks: List[Dict[str, Any]]) -> dict:
    if not checks:
        return _empty_alert("还没有检测结果，请先运行探测。")
    rows = [
        {
            "component": "tr",
            "content": [
                _td(_state_text(str(item.get("state") or "")), "text-no-wrap"),
                _td(item.get("name") or "-", "text-no-wrap"),
                _td(_clip_text(item.get("detail") or "-", 1800)),
                _td(f"{item.get('duration')} 秒" if item.get("duration") is not None else "-", "text-no-wrap"),
            ],
        }
        for item in checks
        if isinstance(item, dict)
    ]
    return {
        "component": "VCard",
        "props": {"variant": "outlined"},
        "content": [
            {
                "component": "VCardText",
                "content": [
                    {"component": "div", "props": {"class": "text-subtitle-2 mb-2"}, "text": "检测明细"},
                    {
                        "component": "VTable",
                        "props": {"density": "compact"},
                        "content": [
                            {
                                "component": "thead",
                                "content": [{
                                    "component": "tr",
                                    "content": [_th("状态"), _th("项目"), _th("结果"), _th("耗时")],
                                }],
                            },
                            {"component": "tbody", "content": rows},
                        ],
                    },
                ],
            }
        ],
    }


def _render_nodes() -> List[str]:
    dri = Path("/dev/dri")
    if not dri.exists():
        return []
    try:
        return sorted(str(item) for item in dri.iterdir() if item.name.startswith("renderD"))
    except Exception:
        return []


def _detail_has_gpu(detail: Any) -> bool:
    text = str(detail or "").upper()
    return "GPU" in text or "LEVEL_ZERO" in text


def _looks_like_missing_openvino(detail: Any) -> bool:
    text = str(detail or "")
    return "ModuleNotFoundError" in text or "No module named" in text or "import_error" in text


def _has_success(checks: List[Dict[str, Any]], name: str) -> bool:
    return any(item.get("name") == name and item.get("state") == "success" for item in checks)


def _check(name: str, state: str, detail: Any, duration: Optional[float] = None) -> Dict[str, Any]:
    item = {
        "name": name,
        "state": state,
        "detail": _clip_text(detail, 3000),
    }
    if duration is not None:
        item["duration"] = round(duration, 2)
    return item


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return default


def _int_or_default(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _clip_text(value: Any, limit: int = 1000) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...(已截断)"


def _col(cols: int, md: Optional[int], child: dict) -> dict:
    props = {"cols": cols}
    if md:
        props["md"] = md
    return {"component": "VCol", "props": props, "content": [child]}


def _metric_col(title: str, value: Any, subtitle: Any) -> dict:
    return {
        "component": "VCol",
        "props": {"cols": 6, "md": 3},
        "content": [{
            "component": "div",
            "props": {"class": "py-2"},
            "content": [
                {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": str(title)},
                {"component": "div", "props": {"class": "text-h6"}, "text": str(value)},
                {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": str(subtitle)},
            ],
        }],
    }


def _status_chip(text: str, color: str) -> dict:
    return {
        "component": "VChip",
        "props": {"color": color, "variant": "tonal", "size": "small"},
        "text": text,
    }


def _action_button(text: str, icon: str, color: str, api: str) -> dict:
    return {
        "component": "VBtn",
        "props": {
            "variant": "tonal",
            "color": color,
            "prepend-icon": icon,
            "size": "small",
        },
        "text": text,
        "events": {
            "click": {
                "api": api,
                "method": "post",
            }
        },
    }


def _empty_alert(text: str) -> dict:
    return {
        "component": "VAlert",
        "props": {
            "type": "info",
            "variant": "tonal",
            "text": text,
        },
    }


def _th(text: str) -> dict:
    return {"component": "th", "text": text}


def _td(text: Any, class_name: Optional[str] = None) -> dict:
    props = {"class": class_name} if class_name else {}
    return {"component": "td", "props": props, "text": "" if text is None else str(text)}


def _state_text(state: str) -> str:
    return {
        "success": "可用",
        "warning": "警告",
        "error": "失败",
        "running": "运行中",
        "skipped": "跳过",
        "idle": "未运行",
    }.get(str(state or ""), str(state or "-"))


def _state_color(state: str) -> str:
    return {
        "success": "success",
        "warning": "warning",
        "error": "error",
        "running": "primary",
        "skipped": "secondary",
        "idle": "secondary",
    }.get(str(state or ""), "secondary")


def _alert_type(state: str) -> str:
    return {
        "success": "success",
        "warning": "warning",
        "error": "error",
        "running": "info",
    }.get(str(state or ""), "info")
