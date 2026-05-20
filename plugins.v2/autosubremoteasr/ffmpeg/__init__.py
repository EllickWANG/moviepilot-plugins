import json
import os
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
    def stream_audio_chunks_from_video(video_path, output_dir, audio_index=None, segment_seconds=600,
                                       stop_event=None, threads=None, chunk_callback=None):
        """
        分段提取音频，并在分段文件关闭后回调处理。
        运行中保留最后一个正在写入的分段，等下一段出现或ffmpeg退出后再处理。
        """
        if not video_path or not output_dir:
            return False, 0, "视频路径或输出目录为空"

        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            return False, 0, str(e)[:1000]

        segment_seconds = max(60, int(segment_seconds or 600))
        audio_stream_index = 0 if audio_index is None else int(audio_index)
        output_pattern = os.path.join(output_dir, "chunk_%05d.mp3")
        command = [
            'ffmpeg', "-hide_banner", "-loglevel", "warning",
            "-threads", str(max(1, int(threads or 1))),
            '-y', '-i', video_path,
            '-map', f'0:a:{audio_stream_index}',
            '-vn', '-sn', '-dn',
            '-ac', '1', '-ar', '24000', '-b:a', '64k',
            '-f', 'segment',
            '-segment_time', str(segment_seconds),
            '-reset_timestamps', '1',
            output_pattern
        ]

        process = None
        processed = set()
        stderr_lines = []

        def list_chunks():
            return sorted(
                os.path.join(output_dir, file_name)
                for file_name in os.listdir(output_dir)
                if file_name.startswith("chunk_") and file_name.endswith(".mp3")
            )

        def handle_ready_chunks(final=False):
            chunks = [chunk for chunk in list_chunks() if chunk not in processed and os.path.getsize(chunk) > 0]
            ready_chunks = chunks if final else chunks[:-1]
            for chunk in ready_chunks:
                processed.add(chunk)
                if chunk_callback:
                    try:
                        chunk_callback(chunk)
                    except Exception as e:
                        raise RuntimeError(f"处理音频分段失败: {e}") from e

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
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
                    return False, len(processed), "用户中断当前任务"

                if process.stderr:
                    readable, _, _ = select.select([process.stderr], [], [], 0.2)
                    if readable:
                        line = process.stderr.readline()
                        if line:
                            stderr_lines.append(line.strip())
                            stderr_lines = stderr_lines[-20:]

                ret = process.poll()
                if ret is None:
                    handle_ready_chunks(final=False)
                else:
                    break
                time.sleep(0.5)

            if process.stderr:
                rest = process.stderr.read()
                if rest:
                    stderr_lines.extend(line.strip() for line in rest.splitlines() if line.strip())
                    stderr_lines = stderr_lines[-20:]
            ret = process.wait()
            if ret != 0:
                return False, len(processed), ("\n".join(stderr_lines) or f"ffmpeg退出码：{ret}")[:1000]
            handle_ready_chunks(final=True)
            if not processed:
                return False, 0, "未生成音频分段"
            return True, len(processed), ""
        except Exception as e:
            if process and process.poll() is None:
                try:
                    process.kill()
                except Exception:
                    pass
            return False, len(processed), str(e)[:1000]

    @staticmethod
    def extract_audio_sample_from_video(video_path, output_path, audio_index=None, start_seconds=0,
                                        duration_seconds=12, stop_event=None, threads=None):
        """
        从指定位置提取一个短音频样本，用于全局语言探测。
        """
        if not video_path or not output_path:
            return False, "视频路径或输出路径为空"

        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
        except Exception as e:
            return False, str(e)[:1000]

        audio_stream_index = 0 if audio_index is None else int(audio_index)
        command = [
            'ffmpeg', "-hide_banner", "-loglevel", "warning",
            "-threads", str(max(1, int(threads or 1))),
            "-ss", str(max(0, float(start_seconds or 0))),
            "-t", str(max(3, float(duration_seconds or 12))),
            '-y', '-i', video_path,
            '-map', f'0:a:{audio_stream_index}',
            '-vn', '-sn', '-dn',
            '-ac', '1', '-ar', '24000', '-b:a', '64k',
            output_path
        ]
        return Ffmpeg._run_command(command, stop_event=stop_event)

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
