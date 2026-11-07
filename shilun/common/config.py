from dataclasses import dataclass, field
import os
from pathlib import Path

DEFAULT_TUSHARE_TOKEN = ""
DEFAULT_TUSHARE_BASE_URL = "https://tt.xiaodefa.cn"
DEFAULT_TUSHARE_TIMEOUT = 10
DEFAULT_TUSHARE_MIN_INTERVAL_SECONDS = 0.55
DEFAULT_TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_MONGO_URI = ""
DEFAULT_MONGO_DB = "shilun"
DEFAULT_KLINESHARE_BASE_URL = "https://data.klineshare.cn"
DEFAULT_KLINESHARE_TIMEOUT = 15
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000
DEFAULT_API_LOG_LEVEL = "info"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path = Path("data")
    db_path: Path = Path("data/shilun.sqlite3")
    log_dir: Path = Path("logs")
    rule_version: str = "v0.2.0"
    feature_version: str = "v0.1.0"
    llm_model_version: str = "openclaw-v1"
    template_version: str = "v0.2.0"
    policy_dir: Path = Path("shilun/config")
    tushare_token: str | None = None
    tushare_base_url: str | None = None
    tushare_timeout: int = 10
    tushare_min_interval_seconds: float = DEFAULT_TUSHARE_MIN_INTERVAL_SECONDS
    telegram_bot_token: str | None = None
    telegram_webhook_base_url: str | None = None
    telegram_webhook_secret: str | None = None
    telegram_api_base: str = DEFAULT_TELEGRAM_API_BASE
    telegram_allowed_chat_ids: tuple[int, ...] = field(default_factory=tuple)
    telegram_push_chat_ids: tuple[int, ...] = field(default_factory=tuple)
    feishu_webhook_url: str | None = None
    mongo_uri: str | None = None
    mongo_db: str = DEFAULT_MONGO_DB
    theme_archive_backend: str = "local"
    klineshare_api_key: str | None = None
    klineshare_base_url: str = DEFAULT_KLINESHARE_BASE_URL
    klineshare_timeout: int = DEFAULT_KLINESHARE_TIMEOUT
    klineshare_include_depth: bool = False
    intraday_notifications_enabled: bool = True
    api_host: str = DEFAULT_API_HOST
    api_port: int = DEFAULT_API_PORT
    api_log_level: str = DEFAULT_API_LOG_LEVEL
    auth_required: bool = False
    auth_username: str | None = None
    auth_password: str | None = None


def _read_env_file() -> dict[str, str]:
    env_path = Path(".env")
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _env(key: str, file_values: dict[str, str], default: str | None = None) -> str | None:
    return os.getenv(key) or file_values.get(key) or default


def _parse_chat_ids(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return ()
    values: list[int] = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            values.append(int(candidate))
        except ValueError:
            continue
    return tuple(values)


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(raw: str | None, default: int) -> int:
    try:
        return int(str(raw).strip()) if raw is not None else default
    except (TypeError, ValueError):
        return default


def load_config() -> AppConfig:
    """返回默认配置"""
    env_file = _read_env_file()
    return AppConfig(
        tushare_token=_env("SHILUN_TUSHARE_TOKEN", env_file)
        or _env("TUSHARE_TOKEN", env_file)
        or DEFAULT_TUSHARE_TOKEN,
        tushare_base_url=_env("SHILUN_TUSHARE_BASE_URL", env_file)
        or _env("TUSHARE_BASE_URL", env_file)
        or DEFAULT_TUSHARE_BASE_URL,
        tushare_timeout=int(_env("SHILUN_TUSHARE_TIMEOUT", env_file, str(DEFAULT_TUSHARE_TIMEOUT)) or DEFAULT_TUSHARE_TIMEOUT),
        tushare_min_interval_seconds=float(
            _env(
                "SHILUN_TUSHARE_MIN_INTERVAL_SECONDS",
                env_file,
                str(DEFAULT_TUSHARE_MIN_INTERVAL_SECONDS),
            )
            or DEFAULT_TUSHARE_MIN_INTERVAL_SECONDS
        ),
        telegram_bot_token=_env("SHILUN_TELEGRAM_BOT_TOKEN", env_file),
        telegram_webhook_base_url=_env("SHILUN_TELEGRAM_WEBHOOK_BASE_URL", env_file),
        telegram_webhook_secret=_env("SHILUN_TELEGRAM_WEBHOOK_SECRET", env_file),
        telegram_api_base=_env("SHILUN_TELEGRAM_API_BASE", env_file, DEFAULT_TELEGRAM_API_BASE) or DEFAULT_TELEGRAM_API_BASE,
        telegram_allowed_chat_ids=_parse_chat_ids(_env("SHILUN_TELEGRAM_ALLOWED_CHAT_IDS", env_file)),
        telegram_push_chat_ids=_parse_chat_ids(
            _env("SHILUN_TELEGRAM_PUSH_CHAT_IDS", env_file) or _env("SHILUN_TELEGRAM_ALLOWED_CHAT_IDS", env_file)
        ),
        feishu_webhook_url=(
            _env("SHILUN_FEISHU_WEBHOOK_URL_OVERRIDE", env_file)
            or _env("SHILUN_FEISHU_WEBHOOK_URL", env_file)
        ),
        mongo_uri=_env("SHILUN_MONGO_URI", env_file, DEFAULT_MONGO_URI) or None,
        mongo_db=_env("SHILUN_MONGO_DB", env_file, DEFAULT_MONGO_DB) or DEFAULT_MONGO_DB,
        theme_archive_backend=_env("SHILUN_THEME_ARCHIVE_BACKEND", env_file, "local") or "local",
        klineshare_api_key=_env("SHILUN_KLINESHARE_API_KEY", env_file),
        klineshare_base_url=(
            _env("SHILUN_KLINESHARE_BASE_URL", env_file, DEFAULT_KLINESHARE_BASE_URL)
            or DEFAULT_KLINESHARE_BASE_URL
        ),
        klineshare_timeout=int(
            _env("SHILUN_KLINESHARE_TIMEOUT", env_file, str(DEFAULT_KLINESHARE_TIMEOUT))
            or DEFAULT_KLINESHARE_TIMEOUT
        ),
        klineshare_include_depth=_parse_bool(_env("SHILUN_KLINESHARE_INCLUDE_DEPTH", env_file)),
        intraday_notifications_enabled=_parse_bool(
            _env("SHILUN_INTRADAY_NOTIFICATIONS_ENABLED", env_file),
            default=True,
        ),
        api_host=_env("SHILUN_API_HOST", env_file, DEFAULT_API_HOST) or DEFAULT_API_HOST,
        api_port=_parse_int(_env("SHILUN_API_PORT", env_file), DEFAULT_API_PORT),
        api_log_level=(
            _env("SHILUN_API_LOG_LEVEL", env_file, DEFAULT_API_LOG_LEVEL)
            or DEFAULT_API_LOG_LEVEL
        ),
        auth_required=_parse_bool(_env("SHILUN_AUTH_REQUIRED", env_file), default=False),
        auth_username=_env("SHILUN_AUTH_USERNAME", env_file),
        auth_password=_env("SHILUN_AUTH_PASSWORD", env_file),
    )
