from dataclasses import dataclass, field
import os
from pathlib import Path

DEFAULT_TUSHARE_TOKEN = "b5c61c3e8cffd9b1b4abd9bce295f068b652843e57f7663e707b8b57"
DEFAULT_TUSHARE_BASE_URL = "https://tt.xiaodefa.cn"
DEFAULT_TUSHARE_TIMEOUT = 10
DEFAULT_TUSHARE_MIN_INTERVAL_SECONDS = 0.55
DEFAULT_TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_MONGO_URI = ""
DEFAULT_MONGO_DB = "shilun"


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
        feishu_webhook_url=_env("SHILUN_FEISHU_WEBHOOK_URL", env_file),
        mongo_uri=_env("SHILUN_MONGO_URI", env_file, DEFAULT_MONGO_URI) or None,
        mongo_db=_env("SHILUN_MONGO_DB", env_file, DEFAULT_MONGO_DB) or DEFAULT_MONGO_DB,
    )
