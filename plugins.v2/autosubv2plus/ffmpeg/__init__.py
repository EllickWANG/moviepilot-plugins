import json
import subprocess


class Ffmpeg:
    @staticmethod
    def check_video_integrity(video_path, duration=None, progress_callback=None):
        """
        完整解码扫描视频和音频流，判断文件是否可完整读取。
        """
        if not video_path:
            return False, "视频路径为空"

        command = [
            'ffmpeg', "-hide_banner", "-nostats", "-v", "error", "-xerror",
            "-progress", "pipe:1",
            "-i", video_path,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-f", "null", "-"
        ]

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            if process.stdout:
                for line in process.stdout:
                    line = line.strip()
                    if not line.startswith("out_time_ms=") or not progress_callback or not duration:
                        continue
                    try:
                        out_time = int(line.split("=", 1)[1]) / 1000000
                        progress_callback(out_time, duration)
                    except Exception:
                        continue
            stderr = process.stderr.read() if process.stderr else ""
            ret = process.wait()
            if ret == 0:
                return True, ""
            return False, (stderr.strip() or f"ffmpeg退出码：{ret}")[:1000]
        except Exception as e:
            return False, str(e)[:1000]

    @staticmethod
    def extract_wav_from_video(video_path, audio_path, audio_index=None):
        """
        使用ffmpeg从视频文件中提取16000hz, 16-bit的wav格式音频
        """
        if not video_path or not audio_path:
            return False

        # 提取指定音频流
        if audio_index:
            command = ['ffmpeg', "-hide_banner", "-loglevel", "warning", '-y', '-i', video_path,
                       '-map', f'0:a:{audio_index}',
                       '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '16000', audio_path]
        else:
            command = ['ffmpeg', "-hide_banner", "-loglevel", "warning", '-y', '-i', video_path,
                       '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '16000', audio_path]

        ret = subprocess.run(command).returncode
        if ret == 0:
            return True
        return False

    @staticmethod
    def get_video_metadata(video_path):
        """
        获取视频元数据
        """
        if not video_path:
            return False

        try:
            command = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', video_path]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode == 0:
                return json.loads(result.stdout.decode("utf-8"))
        except Exception as e:
            print(e)
        return None

    @staticmethod
    def extract_subtitle_from_video(video_path, subtitle_path, subtitle_index=None):
        """
        从视频中提取字幕
        """
        if not video_path or not subtitle_path:
            return False

        if subtitle_index:
            command = ['ffmpeg', "-hide_banner", "-loglevel", "warning", '-y', '-i', video_path,
                       '-map', f'0:s:{subtitle_index}',
                       subtitle_path]
        else:
            command = ['ffmpeg', "-hide_banner", "-loglevel", "warning", '-y', '-i', video_path, subtitle_path]
        ret = subprocess.run(command).returncode
        if ret == 0:
            return True
        return False
