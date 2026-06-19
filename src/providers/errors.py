from urllib.parse import urlsplit, urlunsplit


class ProviderError(RuntimeError):
    def __init__(
        self,
        message,
        *,
        provider="",
        model="",
        base_url="",
        code="provider_error",
        http_status=None,
        retryable=False,
        attempts=1,
        retry_count=0,
        body_excerpt="",
        cause_type="",
    ):
        super().__init__(message)
        self.provider = str(provider or "")
        self.model = str(model or "")
        self.base_url = sanitize_url(base_url)
        self.code = str(code or "provider_error")
        self.http_status = http_status
        self.retryable = bool(retryable)
        self.attempts = int(attempts or 1)
        self.retry_count = int(retry_count or 0)
        self.body_excerpt = _clip(body_excerpt, 500)
        self.cause_type = str(cause_type or "")


def _clip(value: str, limit: int):
    if not isinstance(value, str):
        return value
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
