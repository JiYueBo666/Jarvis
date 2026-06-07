from urllib.parse import urlsplit, urlunsplit


class ProviderError(RuntimeError):
    def __init__(
        self,
        message,  # 错误提示文字
        *,
        provider="",  # 服务商：openai / deepseek / anthropic
        model="",  # 模型名：gpt-4o / deepseek-chat
        base_url="",  # 接口地址
        code="provider_error",  # 错误代码
        http_status=None,  # HTTP 状态码：404 / 500 / 429
        retryable=False,  # 能不能自动重试
        attempts=1,  # 试了几次
        retry_count=0,  # 重试次数
        body_excerpt="",  # 接口返回的错误内容片段
        cause_type="",  # 错误原因类型
    ):
        super().__init__(message)
        self.provider = str(provider or "")
        self.model = str(model or "")
        self.base_url = sanitize_url(base_url)  # 清洗URL，去掉密钥
        self.code = str(code or "provider_error")
        self.http_status = http_status
        self.retryable = bool(retryable)
        self.attempts = int(attempts or 1)
        self.retry_count = int(retry_count or 0)
        self.body_excerpt = _clip(body_excerpt, 500)  # 截断太长的错误
        self.cause_type = str(cause_type or "")

    def to_dict(self):
        payload = {
            "provider_error": {
                "code": self.code,
                "retryable": self.retryable,
                "attemps": self.attempts,
                "retry_count": self.retry_count,
            }
        }

        error = payload["provider_error"]
        if self.provider:
            error["provider"] = self.provider
        if self.model:
            error["model"] = self.model
        if self.base_url:
            error["base_url"] = self.base_url
        if self.http_status is not None:
            error["http_status"] = int(self.http_status)
        if self.body_excerpt:
            error["body_excerpt"] = self.body_excerpt
        if self.cause_type:
            error["cause_type"] = self.cause_type
        return payload


def _clip(value: str, limit: int):
    """
    将value裁剪到limit长度
    """
    if not isinstance(value, str):
        return
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def sanitize_url(value):
    text = str(value or "")
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text.split("?", 1)[0].split("#", 1)[0]
    hostname = parsed.hostname or ""
    if not hostname:
        return urlunsplit((parsed.scheme, "", parsed.path, "", ""))
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
