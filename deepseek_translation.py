"""DeepSeek translation adapter imported from the 1.zip code line.

The adapter is optional: the desktop app continues to use the local Ollama
translator by default, while a user-provided DEEPSEEK_API_KEY can enable the
cloud provider from the engine selector.  No credential is stored in source.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request


class DeepSeekTranslator:
    def __init__(self, api_key: str = "", model: str = "deepseek-chat",
                 endpoint: str = "https://api.deepseek.com/chat/completions",
                 timeout: int = 90):
        self.api_key = (api_key or "").strip()
        self.model = model or "deepseek-chat"
        self.endpoint = endpoint or "https://api.deepseek.com/chat/completions"
        self.timeout = int(timeout)

    @staticmethod
    def parse_result(content: str, count: int) -> list[str]:
        """Parse numbered DeepSeek output while preserving segment order."""
        rows: dict[int, str] = {}
        for line in (content or "").splitlines():
            line = line.strip()
            if not line:
                continue
            match = re.match(r"^(\d+)[\.、:\s]+(.+)$", line)
            if match:
                rows[int(match.group(1))] = match.group(2).strip()
        if len(rows) != count or any(i not in rows or not rows[i] for i in range(1, count + 1)):
            raise ValueError(f"DeepSeek 返回译文段数不匹配，应为 {count} 段")
        return [rows[i] for i in range(1, count + 1)]

    def translate(self, texts: list[str], source_language: str = "") -> list[str]:
        if not texts:
            raise RuntimeError("没有可翻译的口播内容。")
        if not self.api_key:
            raise RuntimeError("未配置 DeepSeek API Key，请在软件目录放置 deepseek_api_key.txt，或设置 DEEPSEEK_API_KEY。")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise RuntimeError("口播包含空段落，无法可靠对齐译文。")

        lines = "\n".join(f"{i}. {text}" for i, text in enumerate(texts, 1))
        prompt = (
            "请将以下短视频口播字幕翻译为流畅自然的简体中文，适用于电商带货语境。"
            "严格按原编号逐行输出，不要合并、漏译、解释或改写价格、币种、数量、折扣和品牌：\n" + lines
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是专业的短视频口播字幕翻译助手。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.25,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                translated = self.parse_result(content, len(texts))
                if any(not text.strip() for text in translated):
                    raise ValueError("DeepSeek 返回了空译文")
                return translated
            except urllib.error.HTTPError as exc:
                detail = exc.read(2048).decode("utf-8", errors="replace")
                last_error = RuntimeError(f"DeepSeek 请求失败（HTTP {exc.code}）：{detail}")
                if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                    raise last_error from exc
            except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 2:
                    raise RuntimeError(f"DeepSeek 返回内容无法解析：{exc}") from exc
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"DeepSeek 翻译失败：{last_error}")
