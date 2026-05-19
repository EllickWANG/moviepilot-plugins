import json
import select
import subprocess
import time


class Ffmpeg:
    @staticmethod
    def _run_command(command, stop_event=None, progress_callback=None, duration=None):
        process = None
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            while True:
                if stop_event and stop_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                    return False, "用户中断当前任务"

                line = ""
                if process.stdout:
                    readable, _, _ = select.select([process.stdout], [], [], 0.2)
                    if readable:
                        line = process.stdout.readline()
                if line:
                    line = line.strip()
                    if line.startswith("out_time_ms=") and progress_callback and duration:
                        try:
                            progress_callback(int(line.split("=", 1)[1]) / 1000000, duration)
                        except Exception:
                            pass
                    continue

                if process.poll() is not None:
                    break

            stderr = process.stderr.read() if process.stderr else ""
            ret = process.wait()
            if ret == 0:
                return True, ""
            return False, (stderr.strip() or f"ffmpeg退出码：{ret}")[:1000]
        except Exception as e:
            if process and process.poll() is None:
                try:
                    process.kill()
                except Exception:
                    pass
            return False, str(e)[:1000]

    @staticmethod
    def check_video_integrity(video_path, duration=None, progress_callback=None, stop_event=None, threads=None):
        """
        完整解码扫描视频和音频流，判断文件是否可完整读取。
        """
        if not video_path:
            return False, "视频路径为空"

        command = [
            'ffmpeg', "-hide_banner", "-nostats", "-v", "error", "-xerror",
            "-threads", str(max(1, int(threads or 1))),
            "-progress", "pipe:1",
            "-i", video_path,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-f", "null", "-"
        ]

        return Ffmpeg._run_command(command, stop_event=stop_event, progress_callback=progress_callback,
                                   duration=duration)

    @staticmethod
    def extract_wav_from_video(video_path, audio_path, audio_index=None, stop_event=None, threads=None):
        """
        使用ffmpeg从视频文件中提取16000hz, 16-bit的wav格式音频
        """
        if not video_path or not audio_path:
            return False

        # 提取指定音频流
        if audio_index:
            command = ['ffmpeg', "-hide_banner", "-loglevel", "warning", "-threads", str(max(1, int(threads or 1))),
                       '-y', '-i', video_path,
                       '-map', f'0:a:{audio_index}',
                       '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '16000', audio_path]
        else:
            command = ['ffmpeg', "-hide_banner", "-loglevel", "warning", "-threads", str(max(1, int(threads or 1))),
                       '-y', '-i', video_path,
                       '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '16000', audio_path]

        ok, _ = Ffmpeg._run_command(command, stop_event=stop_event)
        return ok

    @staticmethod
    def get_video_metadata(video_path, stop_event=None):
        """
        获取视频元数据
        """
        if not video_path:
            return False

        try:
            command = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', video_path]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if stop_event and stop_event.is_set():
                return None
            if result.returncode == 0:
                return json.loads(result.stdout.decode("utf-8"))
        except Exception as e:
            print(e)
        return None

    @staticmethod
    def extract_subtitle_from_video(video_path, subtitle_path, subtitle_index=None, stop_event=None, threads=None):
        """
        从视频中提取字幕
        """
        if not video_path or not subtitle_path:
            return False

        if subtitle_index:
            command = ['ffmpeg', "-hide_banner", "-loglevel", "warning", "-threads", str(max(1, int(threads or 1))),
                       '-y', '-i', video_path,
                       '-map', f'0:s:{subtitle_index}',
                       subtitle_path]
        else:
            command = ['ffmpeg', "-hide_banner", "-loglevel", "warning", "-threads", str(max(1, int(threads or 1))),
                       '-y', '-i', video_path, subtitle_path]
        ok, _ = Ffmpeg._run_command(command, stop_event=stop_event)
        return ok
