from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import time
from typing import Any
import pandas as pd

try:
    import tushare as ts
except ImportError:  # pragma: no cover
    ts = None


# Tokens belong in the local ignored .env (or the process environment).  Keep
# the fallback empty so a rotated credential can never silently fall back to a
# stale credential embedded in source.
DEFAULT_TUSHARE_TOKEN = ""
DEFAULT_TUSHARE_BASE_URL = "https://tt.xiaodefa.cn"
DEFAULT_TUSHARE_TIMEOUT = 10
DEFAULT_TUSHARE_MIN_INTERVAL_SECONDS = 0.55
NON_RETRYABLE_ERROR_MARKERS = (
    "token不对",
    "token无效",
    "没有权限",
    "无权限",
    "权限不足",
)


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


class BarCleaner:
    """Normalize and validate daily OHLCV bars before analysis."""

    COLUMN_ALIASES = {
        "ticker": "ticker",
        "证券代码": "ticker",
        "code": "ticker",
        "symbol": "ticker",
        "name": "name",
        "证券名称": "name",
        "date": "date",
        "交易时间": "date",
        "trade_date": "date",
        "open": "open",
        "开盘价": "open",
        "high": "high",
        "最高价": "high",
        "low": "low",
        "最低价": "low",
        "close": "close",
        "收盘价": "close",
        "volume": "volume",
        "成交量": "volume",
        "amount": "amount",
        "成交额": "amount",
    }
    REQUIRED_COLUMNS = ["ticker", "date", "open", "high", "low", "close", "volume"]

    def clean_bars(self, df: pd.DataFrame) -> pd.DataFrame:
        normalized = self._normalize_columns(df)
        missing_cols = [column for column in self.REQUIRED_COLUMNS if column not in normalized.columns]
        if missing_cols:
            raise ValueError(f"{missing_cols}列缺失")

        normalized = normalized.drop_duplicates(subset=["ticker", "date"])
        normalized = normalized.dropna(subset=self.REQUIRED_COLUMNS)
        valid = (
            (normalized["low"] <= normalized[["close", "open", "high"]].min(axis=1))
            & (normalized["high"] >= normalized[["close", "open", "low"]].max(axis=1))
            & (normalized["low"] <= normalized["high"])
        )
        return normalized[valid].sort_values(by=["ticker", "date"], ascending=True).reset_index(drop=True)

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        renamed = df.rename(
            columns={column: self.COLUMN_ALIASES.get(str(column).strip(), str(column).strip()) for column in df.columns}
        ).copy()
        if "ticker" in renamed.columns:
            renamed["ticker"] = renamed["ticker"].astype("string").str.strip()
            renamed = renamed[~renamed["ticker"].str.startswith("数据来源：", na=True)]
        if "date" in renamed.columns:
            renamed["date"] = pd.to_datetime(renamed["date"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            if column in renamed.columns:
                renamed[column] = pd.to_numeric(renamed[column], errors="coerce")
        return renamed


@dataclass(frozen=True)
class ImportResult:
    imported_rows: int
    skipped_rows: int
    error_rows: int


@dataclass(frozen=True)
class TushareConfig:
    token: str
    base_url: str
    timeout: int = 10
    min_interval_seconds: float = DEFAULT_TUSHARE_MIN_INTERVAL_SECONDS

    # 定义类的方法，cls即当前class：TushareConfig
    @classmethod
    def from_env(cls) -> "TushareConfig":
        env_file = _read_env_file()
        # 先从环境变量1获取，再是环境变量2，实在不行从明文读
        token = (
            _env("TUSHARE_TOKEN", env_file)
            or _env("SHILUN_TUSHARE_TOKEN", env_file)
            or DEFAULT_TUSHARE_TOKEN
        )
        base_url = (
            _env("TUSHARE_BASE_URL", env_file)
            or _env("SHILUN_TUSHARE_BASE_URL", env_file)
            or DEFAULT_TUSHARE_BASE_URL
        )
        timeout = int(_env("SHILUN_TUSHARE_TIMEOUT", env_file, str(DEFAULT_TUSHARE_TIMEOUT)) or DEFAULT_TUSHARE_TIMEOUT)
        min_interval_seconds = float(
            _env("SHILUN_TUSHARE_MIN_INTERVAL_SECONDS", env_file, str(DEFAULT_TUSHARE_MIN_INTERVAL_SECONDS))
            or DEFAULT_TUSHARE_MIN_INTERVAL_SECONDS
        )
        return cls(token, base_url, timeout, min_interval_seconds)


class TushareDailyClient:
    # 获取tushare日线数据+基础数据整理
    def __init__(self, config: TushareConfig, pro_client: Any | None = None) -> None:
        self.config = config
        self.pro_client = pro_client or self._build_client()
        self._last_call_monotonic = 0.0

    def _build_client(self) -> Any:
        if ts is None:
            raise ImportError("Tushare包不存在")
        client = ts.pro_api(self.config.token)
        # Private gateways rely on Tushare's internal DataApi fields rather than
        # the public attributes, so set both to keep compatibility with the
        # purchased endpoint usage pattern.
        client._DataApi__token = self.config.token
        client._DataApi__http_url = self.config.base_url
        client.base_url = self.config.base_url
        client.timeout = self.config.timeout
        return client

    def _respect_rate_limit(self) -> None:
        min_interval = max(0.0, float(self.config.min_interval_seconds))
        if min_interval <= 0 or self._last_call_monotonic <= 0:
            return
        elapsed = time.monotonic() - self._last_call_monotonic
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    def _call_api(self, api_name: str, /, **kwargs: Any) -> Any:
        # Private gateways may occasionally reset the connection under load.
        # Retry transient errors, but fail fast on permanent auth/permission
        # errors so health checks do not hang for avoidable reasons.
        last_error: Exception | None = None
        for attempt in range(1, 7):
            try:
                api = getattr(self.pro_client, api_name)
                self._respect_rate_limit()
                try:
                    return api(**kwargs)
                finally:
                    self._last_call_monotonic = time.monotonic()
            except Exception as exc:  # pragma: no cover - depends on gateway stability
                last_error = exc
                if attempt == 6:
                    raise
                message = str(exc)
                if "访问频率太高" in message or "40201" in message:
                    time.sleep(5.0 * attempt)
                elif any(marker in message for marker in NON_RETRYABLE_ERROR_MARKERS):
                    raise
                else:
                    time.sleep(0.8 * attempt)
        if last_error is not None:  # pragma: no cover
            raise last_error
        raise RuntimeError(f"Tushare API call failed unexpectedly: {api_name}")

    @staticmethod
    def _normalize_daily_frame(raw_df: pd.DataFrame | None, *, include_reference: bool = False) -> pd.DataFrame:
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(
                columns=["ticker", "date", "open", "high", "low", "close", "volume", "amount"],
            )

        rename_map = {
            "ts_code": "ticker",
            "trade_date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "vol": "volume",
            "amount": "amount",
        }
        df = raw_df.rename(columns=rename_map)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        df["open"] = pd.to_numeric(df["open"], errors="coerce")
        df["high"] = pd.to_numeric(df["high"], errors="coerce")
        df["low"] = pd.to_numeric(df["low"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        final_columns = ["ticker", "date", "open", "high", "low", "close", "volume", "amount"]
        if include_reference:
            df["pre_close"] = pd.to_numeric(df.get("pre_close"), errors="coerce")
            final_columns.append("pre_close")
        for cols in final_columns:
            if cols not in df.columns:
                df[cols] = pd.NA
        df = df[final_columns]
        df = df.sort_values(by=["ticker", "date"]).reset_index(drop=True)
        return df

    def fetch_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        raw_df = self._call_api("daily", ts_code=ts_code, start_date=start_date, end_date=end_date)
        return self._normalize_daily_frame(raw_df)

    def fetch_index_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        raw_df = self._call_api(
            "index_daily",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        return self._normalize_daily_frame(raw_df, include_reference=True)

    def fetch_daily_for_trade_date(self, trade_date: str, *, include_reference: bool = True) -> pd.DataFrame:
        raw_df = self._call_api("daily", trade_date=trade_date)
        return self._normalize_daily_frame(raw_df, include_reference=include_reference)

    def fetch_stock_basic(
        self,
        list_status: str = "L",
        fields: str = "ts_code,symbol,name,area,industry,market,list_date",
    ) -> pd.DataFrame:
        raw_df = self._call_api("stock_basic", exchange="", list_status=list_status, fields=fields)
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(columns=fields.split(","))
        return raw_df.reset_index(drop=True)

    def fetch_trade_calendar(
        self,
        start_date: str,
        end_date: str,
        exchange: str = "SSE",
    ) -> pd.DataFrame:
        raw_df = self._call_api(
            "trade_cal",
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            fields="exchange,cal_date,is_open,pretrade_date",
        )
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(columns=["exchange", "cal_date", "is_open", "pretrade_date"])
        calendar = raw_df.copy()
        calendar["is_open"] = pd.to_numeric(calendar["is_open"], errors="coerce").fillna(0).astype(int)
        return calendar.sort_values("cal_date").reset_index(drop=True)

    def fetch_daily_basic(
        self,
        ts_code: str | None = None,
        trade_date: str | None = None,
        fields: str = (
            "ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,pe,pb,ps_ttm,"
            "dv_ttm,total_share,float_share,free_share,total_mv,circ_mv"
        ),
    ) -> pd.DataFrame:
        raw_df = self._call_api("daily_basic", ts_code=ts_code, trade_date=trade_date, fields=fields)
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(columns=fields.split(","))
        frame = raw_df.reset_index(drop=True)
        if "trade_date" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
        return frame

    def fetch_moneyflow(
        self,
        ts_code: str | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        fields: str = (
            "ts_code,trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount,"
            "buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount"
        ),
    ) -> pd.DataFrame:
        raw_df = self._call_api(
            "moneyflow",
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            fields=fields,
        )
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(columns=fields.split(","))
        frame = raw_df.reset_index(drop=True)
        if "trade_date" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
        amount_columns = [column for column in frame.columns if column.endswith("_amount")]
        for column in amount_columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    def fetch_stock_limits(
        self,
        trade_date: str,
        *,
        offset: int = 0,
        limit: int = 5800,
    ) -> pd.DataFrame:
        """读取一个交易日的涨跌停价，归一为执行层价格事实契约。"""

        normalized_date = str(trade_date).replace("-", "")
        if len(normalized_date) != 8 or not normalized_date.isdigit():
            raise ValueError("trade_date 必须使用 YYYYMMDD 或 YYYY-MM-DD")
        fields = "trade_date,ts_code,pre_close,up_limit,down_limit"
        raw_df = self._call_api(
            "stk_limit",
            trade_date=normalized_date,
            offset=max(0, int(offset)),
            limit=max(1, min(int(limit), 5800)),
            fields=fields,
        )
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(columns=[
                "trade_date", "ticker", "pre_close", "up_limit", "down_limit",
                "source", "source_at",
            ])
        frame = raw_df.rename(columns={"ts_code": "ticker"}).copy()
        for column in ("trade_date", "ticker", "pre_close", "up_limit", "down_limit"):
            if column not in frame.columns:
                frame[column] = pd.NA
        frame["trade_date"] = frame["trade_date"].map(
            lambda value: str(value).replace("-", "")
        )
        for column in ("pre_close", "up_limit", "down_limit"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["source"] = "tushare_stk_limit"
        frame["source_at"] = datetime.now().isoformat(timespec="seconds")
        columns = [
            "trade_date", "ticker", "pre_close", "up_limit", "down_limit",
            "source", "source_at",
        ]
        return (
            frame[columns]
            .dropna(subset=["ticker", "up_limit", "down_limit"])
            .drop_duplicates(subset=["trade_date", "ticker"], keep="last")
            .sort_values("ticker")
            .reset_index(drop=True)
        )

    def fetch_all_stock_limits(
        self,
        trade_date: str,
        *,
        page_size: int = 5800,
    ) -> pd.DataFrame:
        """分页读取全市场涨跌停价，并防止私有网关忽略 offset 后死循环。"""

        size = max(1, min(int(page_size), 5800))
        offset = 0
        pages: list[pd.DataFrame] = []
        seen: set[tuple[str, str]] = set()
        while True:
            page = self.fetch_stock_limits(trade_date, offset=offset, limit=size)
            if page.empty:
                break
            keys = {
                (str(row.trade_date), str(row.ticker))
                for row in page.itertuples(index=False)
            }
            new_keys = keys - seen
            if not new_keys:
                break
            pages.append(page)
            seen.update(new_keys)
            if len(page) < size:
                break
            offset += size
        if not pages:
            return pd.DataFrame(columns=[
                "trade_date", "ticker", "pre_close", "up_limit", "down_limit",
                "source", "source_at",
            ])
        return (
            pd.concat(pages, ignore_index=True)
            .drop_duplicates(subset=["trade_date", "ticker"], keep="last")
            .sort_values("ticker")
            .reset_index(drop=True)
        )

    def fetch_fina_indicator(
        self,
        ts_code: str,
        limit: int = 4,
        fields: str = (
            "ts_code,end_date,ann_date,roe_dt,or_yoy,op_yoy,q_sales_yoy,q_dtprofit_yoy,"
            "grossprofit_margin,debt_to_assets,ocf_to_or,current_ratio,quick_ratio"
        ),
    ) -> pd.DataFrame:
        raw_df = self._call_api("fina_indicator", ts_code=ts_code, fields=fields)
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(columns=fields.split(","))
        frame = raw_df.reset_index(drop=True).head(limit)
        for column in ["end_date", "ann_date"]:
            if column in frame.columns:
                frame[column] = pd.to_datetime(frame[column], format="%Y%m%d", errors="coerce")
        return frame

    def fetch_chip_perf(
        self,
        ts_code: str,
        trade_date: str,
    ) -> pd.DataFrame:
        raw_df = self._call_api("cyq_perf", ts_code=ts_code, trade_date=trade_date)
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(columns=["ts_code", "trade_date"])
        frame = raw_df.reset_index(drop=True)
        if "trade_date" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
        return frame


class TushareDailyImporter:
    def __init__(self, client: TushareDailyClient, cleaner: BarCleaner | None = None) -> None:
        self.client = client
        self.cleaner = cleaner or BarCleaner()

    def import_daily(self, ts_code: str, start_date: str, end_date: str) -> tuple[pd.DataFrame, ImportResult]:
        raw_df = self.client.fetch_daily(ts_code, start_date, end_date)
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(
                columns=["ticker", "date", "open", "high", "low", "close", "volume", "amount"]
            ), ImportResult(
                imported_rows=0,
                skipped_rows=0,
                error_rows=0,
            )
        clean_df = self.cleaner.clean_bars(raw_df)
        total_raws = len(raw_df)
        clean_raws = len(clean_df)
        skipped_rows = total_raws - clean_raws
        result = ImportResult(
            imported_rows=clean_raws,
            skipped_rows=skipped_rows,
            error_rows=0,
        )
        return clean_df, result
