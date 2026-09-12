"""极简 JSON-over-HTTP 客户端（纯标准库）。

`llm.py`（对话补全）和 `embedding.py`（向量化）都在调 OpenAI 兼容的 REST 接口，
把"带重试的 POST JSON"抽出来共用，避免两处各写一遍 urllib。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

# 这些状态码通常是瞬时的，值得重试
_RETRY_STATUS = (408, 409, 425, 429, 500, 502, 503, 504)


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def post_json(
    url: str,
    payload: dict,
    headers: dict | None = None,
    timeout: int = 60,
    retries: int = 2,
    backoff: float = 1.5,
) -> dict:
    """POST 一个 JSON 并返回解析后的 JSON；失败抛 HttpError。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        },
    )

    last_error: HttpError | None = None

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001 - 读不到响应体就算了
                pass
            last_error = HttpError(
                f"HTTP {exc.code} {exc.reason} {detail}".strip(), status=exc.code
            )
            if exc.code in _RETRY_STATUS and attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            break

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = HttpError(f"网络错误：{exc}")
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            break

        except json.JSONDecodeError as exc:
            last_error = HttpError(f"响应不是合法 JSON：{exc}")
            break

    raise last_error or HttpError("未知错误")
