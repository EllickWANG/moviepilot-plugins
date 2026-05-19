import time
import random
import inspect
import json
from types import SimpleNamespace
from typing import List, Union

import httpx
from cacheout import Cache

OpenAISessionCache = Cache(maxsize=100, ttl=3600, timer=time.time, default=None)


def _to_namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _to_namespace(val) for key, val in value.items()})
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    return value


class OpenAi:
    _api_key: str = None
    _api_url: str = None
    _model: str = "gpt-3.5-turbo"
    _timeout: int = 120

    def __init__(self, api_key: str = None, api_url: str = None, proxy: dict = None, model: str = None,
                 compatible: bool = False, timeout: int = 120):
        self._api_key = api_key
        self._api_url = api_url
        try:
            self._timeout = max(5, int(timeout or 120))
        except Exception:
            self._timeout = 120
        base_url = self._api_url.rstrip("/") if self._api_url else "https://api.openai.com"
        if not compatible and not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        self._base_url = base_url

        # 避免 OpenAI SDK 与 httpx 版本不匹配时的 proxies/proxy 参数兼容问题。
        proxy_url = None
        if isinstance(proxy, dict):
            proxy_url = proxy.get("https") or proxy.get("http")
        elif proxy:
            proxy_url = proxy
        client_kwargs = {"timeout": self._timeout}
        if proxy_url:
            httpx_client_params = inspect.signature(httpx.Client).parameters
            if "proxy" in httpx_client_params:
                client_kwargs["proxy"] = proxy_url
            elif "proxies" in httpx_client_params:
                client_kwargs["proxies"] = proxy_url
        self.client = httpx.Client(**client_kwargs)

        if model:
            self._model = model

    @staticmethod
    def __save_session(session_id: str, message: str):
        """
        保存会话
        :param session_id: 会话ID
        :param message: 消息
        :return:
        """
        seasion = OpenAISessionCache.get(session_id)
        if seasion:
            seasion.append({
                "role": "assistant",
                "content": message
            })
            OpenAISessionCache.set(session_id, seasion)

    @staticmethod
    def __get_session(session_id: str, message: str) -> List[dict]:
        """
        获取会话
        :param session_id: 会话ID
        :return: 会话上下文
        """
        seasion = OpenAISessionCache.get(session_id)
        if seasion:
            seasion.append({
                "role": "user",
                "content": message
            })
        else:
            seasion = [
                {
                    "role": "system",
                    "content": "请在接下来的对话中请使用中文回复，并且内容尽可能详细。"
                },
                {
                    "role": "user",
                    "content": message
                }]
            OpenAISessionCache.set(session_id, seasion)
        return seasion

    def __get_model(self, message: Union[str, List[dict]],
                    prompt: str = None,
                    user: str = "MoviePilot",
                    **kwargs):
        """
        获取模型
        """
        if not isinstance(message, list):
            if prompt:
                message = [
                    {
                        "role": "system",
                        "content": prompt
                    },
                    {
                        "role": "user",
                        "content": message
                    }
                ]
            else:
                message = [
                    {
                        "role": "user",
                        "content": message
                    }
                ]
        payload = {
            "model": self._model,
            "user": user,
            "messages": message,
        }
        payload.update(kwargs)
        response = self.client.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            raise RuntimeError(f"OpenAI接口返回错误 {response.status_code}: {response.text[:500]}") from err
        try:
            return _to_namespace(response.json())
        except ValueError as err:
            raise RuntimeError(f"OpenAI接口返回非JSON响应: {response.text[:500]}") from err

    @staticmethod
    def __clear_session(session_id: str):
        """
        清除会话
        :param session_id: 会话ID
        :return:
        """
        if OpenAISessionCache.get(session_id):
            OpenAISessionCache.delete(session_id)

    def translate_to_zh(self, text: str, context: str = None, max_retries: int = 3):
        """
        翻译为中文
        :param text: 输入文本
        :param context: 翻译上下文
        :param max_retries: 最大重试次数
        """
        system_prompt = """您是一位专业字幕翻译专家，请严格遵循以下规则：
1. 将原文精准翻译为简体中文，保持原文本意
2. 使用自然的口语化表达，符合中文观影习惯
3. 结合上下文语境，人物称谓、专业术语、情感语气在上下文中保持连贯
4. 按行翻译待译内容。翻译结果不要包括上下文。
5. 输出内容必须仅包括译文。不要输出任何开场白，解释说明或总结"""
        user_prompt = f"翻译上下文：\n{context}\n\n需要翻译的内容：\n{text}" if context else f"请翻译：\n{text}"

        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                completion = self.__get_model(prompt=system_prompt,
                                              message=user_prompt,
                                              temperature=0.2,
                                              top_p=0.9)
                result = completion.choices[0].message.content.strip()
                return True, result
            except Exception as e:
                last_error = str(e)
                if max_retries <= 0:
                    raise
                if attempt < max_retries:
                    # 使用指数退避和随机抖动，避免多个请求同时重试
                    base_delay = 2 ** attempt  # 指数退避: 1s, 2s, 4s...
                    jitter = random.uniform(0.1, 0.9)  # 随机抖动: 0.1-0.9秒
                    sleep_time = base_delay + jitter
                    print(f"翻译请求失败 (第{attempt + 1}次尝试)：{last_error}，{sleep_time:.1f}秒后重试...")
                    time.sleep(sleep_time)
                else:
                    print(f"翻译请求失败 (已重试{max_retries}次)：{last_error}")
                    return False, f"{last_error}"

    def translate_subtitle_items_to_zh(self, items: List[dict], context: str = None, max_retries: int = 3):
        """
        按编号批量翻译字幕条目，返回严格JSON，便于调用方按id回填。
        """
        system_prompt = """您是一位专业字幕翻译专家，请严格遵循以下规则：
1. 将字幕文本精准翻译为简体中文，保持原文本意
2. 使用自然口语化表达，符合中文观影习惯
3. 结合上下文保持人物称谓、专业术语、情绪语气一致
4. 必须逐条翻译输入items里的text，必须保留每个id
5. 输出必须是合法JSON对象，格式严格为 {"items":[{"id":1,"text":"译文"}]}
6. 不要输出Markdown代码块、解释、总结或任何JSON之外的内容"""
        payload = {
            "context": context or "",
            "items": items or []
        }
        user_prompt = (
            "请翻译下面JSON中的字幕items。只翻译text字段，id原样返回；"
            "items数量必须完全一致，不要合并、拆分、删除任何条目。\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                completion = self.__get_model(prompt=system_prompt,
                                              message=user_prompt,
                                              temperature=0.1,
                                              top_p=0.9)
                result = completion.choices[0].message.content.strip()
                return True, result
            except Exception as e:
                last_error = str(e)
                if max_retries <= 0:
                    raise
                if attempt < max_retries:
                    base_delay = 2 ** attempt
                    jitter = random.uniform(0.1, 0.9)
                    sleep_time = base_delay + jitter
                    print(f"批量字幕翻译请求失败 (第{attempt + 1}次尝试)：{last_error}，{sleep_time:.1f}秒后重试...")
                    time.sleep(sleep_time)
                else:
                    print(f"批量字幕翻译请求失败 (已重试{max_retries}次)：{last_error}")
                    return False, f"{last_error}"
