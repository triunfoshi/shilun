from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from shilun.api import TelegramBotClient
from shilun.common.config import AppConfig, load_config
from shilun.common.db import CandidatePoolStateStore, MarketSnapshotRecordStore, MongoSnapshotStore
from shilun.jobs.snapshot_job import SnapshotJob, SnapshotJobRequest, SnapshotJobResult
from shilun.jobs.snapshot_ranking import build_output_table
from shilun.notifications import FeishuBotClient


@dataclass(frozen=True)
class DailyPushRequest:
    target_date: str | None = None
    top_n: int = 500
    message_top_k: int = 20
    candidate_pool_top_k: int = 10
    max_message_chars: int = 3500
    output_dir: str = "outputs"
    exclude_st: bool = True
    dry_run: bool = False
    fallback_latest_local: bool = False
    prefer_mongo: bool = True
    allow_snapshot_fallback: bool = False
    include_candidate_pool: bool = True


@dataclass(frozen=True)
class DailyPushResult:
    analysis_date: str
    report_path: Path
    csv_path: Path
    pushed_channels: tuple[str, ...]
    failed_channels: tuple[str, ...]
    message_text: str
    data_source: str


@dataclass(frozen=True)
class DailyPushSource:
    analysis_date: str
    scanned_count: int
    skipped_count: int
    report_path: Path
    csv_path: Path
    table: pd.DataFrame
    data_source: str


@dataclass(frozen=True)
class CandidatePoolPushContext:
    states: list[dict[str, Any]]
    events: list[dict[str, Any]]
    note: str = ""


class DailyPushJob:
    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        snapshot_job: SnapshotJob | None = None,
        mongo_store: MongoSnapshotStore | None = None,
        market_snapshot_store: MarketSnapshotRecordStore | None = None,
        candidate_pool_store: CandidatePoolStateStore | None = None,
        telegram_client: TelegramBotClient | None = None,
        feishu_client: FeishuBotClient | None = None,
    ) -> None:
        self.config = config or load_config()
        self.snapshot_job = snapshot_job
        self.mongo_store = mongo_store or self._build_mongo_store()
        self.market_snapshot_store = market_snapshot_store or (
            getattr(self.mongo_store, "market_snapshots", self.mongo_store) if self.mongo_store is not None else None
        )
        self.candidate_pool_store = candidate_pool_store or (
            getattr(self.mongo_store, "candidate_pools", self.mongo_store) if self.mongo_store is not None else None
        )
        self.telegram_client = telegram_client or self._build_telegram_client()
        self.feishu_client = feishu_client or self._build_feishu_client()

    def __del__(self) -> None:  # pragma: no cover
        mongo_store = getattr(self, "mongo_store", None)
        if mongo_store is not None:
            try:
                mongo_store.close()
            except Exception:
                pass

    def run(self, request: DailyPushRequest) -> DailyPushResult:
        source = self._load_push_source(request)
        candidate_pool_context = (
            self._load_candidate_pool_context(
                analysis_date=source.analysis_date,
                exclude_st=request.exclude_st,
                top_k=request.candidate_pool_top_k,
            )
            if request.include_candidate_pool
            else None
        )
        message_text = self._build_message_text(
            analysis_date=source.analysis_date,
            scanned_count=source.scanned_count,
            skipped_count=source.skipped_count,
            table=source.table,
            top_k=request.message_top_k,
            candidate_pool_context=candidate_pool_context,
            candidate_pool_top_k=request.candidate_pool_top_k,
            max_chars=request.max_message_chars,
        )

        pushed_channels: list[str] = []
        failed_channels: list[str] = []
        if request.dry_run:
            pushed_channels.append("dry-run")
        else:
            if self.feishu_client is not None:
                try:
                    self.feishu_client.send_text(message_text)
                    pushed_channels.append("feishu")
                except Exception as error:
                    failed_channels.append(f"feishu:{error}")
            telegram_targets = self._resolve_telegram_chat_ids()
            if self.telegram_client is not None and telegram_targets:
                try:
                    for chat_id in telegram_targets:
                        self.telegram_client.send_message(chat_id=chat_id, text=message_text)
                    pushed_channels.append(f"telegram:{len(telegram_targets)}")
                except Exception as error:
                    failed_channels.append(f"telegram:{error}")
            if not pushed_channels and failed_channels:
                raise RuntimeError("Push failed: " + " | ".join(failed_channels))
            if not pushed_channels:
                # P4 cleanup: avoid the old fake-success path where a report was built but no channel actually sent it.
                raise RuntimeError(
                    "No push channel configured. Set SHILUN_FEISHU_WEBHOOK_URL or "
                    "SHILUN_TELEGRAM_BOT_TOKEN + SHILUN_TELEGRAM_PUSH_CHAT_IDS, "
                    "or run with --dry-run to preview the message only."
                )
        return DailyPushResult(
            analysis_date=source.analysis_date,
            report_path=source.report_path,
            csv_path=source.csv_path,
            pushed_channels=tuple(pushed_channels),
            failed_channels=tuple(failed_channels),
            message_text=message_text,
            data_source=source.data_source,
        )

    def _load_push_source(self, request: DailyPushRequest) -> DailyPushSource:
        target_date = request.target_date or datetime.now().strftime("%Y-%m-%d")
        export_top_n = max(int(request.top_n), int(request.message_top_k))
        if request.prefer_mongo:
            mongo_source = self._load_mongo_push_source(
                analysis_date=target_date,
                top_n=export_top_n,
                output_dir=request.output_dir,
                exclude_st=request.exclude_st,
            )
            if mongo_source is not None:
                return mongo_source
            if not request.allow_snapshot_fallback:
                if request.fallback_latest_local:
                    latest = self._find_latest_local_snapshot(request.output_dir, exclude_st=request.exclude_st)
                    if latest is not None:
                        return self._source_from_snapshot_result(latest, data_source="local_csv")
                raise RuntimeError(
                    "No Mongo market_snapshot_records found for daily push. "
                    "Run SnapshotJob/Tushare sync first, or set allow_snapshot_fallback=True explicitly."
                )

        if not request.allow_snapshot_fallback:
            raise RuntimeError("Snapshot fallback is disabled; daily push will not call Tushare implicitly.")

        try:
            snapshot_job = self.snapshot_job or SnapshotJob(config=self.config)
            snapshot_result = snapshot_job.run(
                SnapshotJobRequest(
                    target_date=target_date,
                    top_n=export_top_n,
                    output_dir=request.output_dir,
                    exclude_st=request.exclude_st,
                    prefer_mongo_data=request.prefer_mongo,
                    allow_tushare_fallback=True,
                )
            )
            return self._source_from_snapshot_result(snapshot_result, data_source="snapshot_fallback")
        except Exception:
            if not request.fallback_latest_local:
                raise
            latest = self._find_latest_local_snapshot(request.output_dir, exclude_st=request.exclude_st)
            if latest is None:
                raise
            return self._source_from_snapshot_result(latest, data_source="local_csv")

    def _load_mongo_push_source(
        self,
        *,
        analysis_date: str,
        top_n: int,
        output_dir: str,
        exclude_st: bool,
    ) -> DailyPushSource | None:
        if self.market_snapshot_store is None:
            return None
        records = self.market_snapshot_store.find_market_snapshot_records(
            analysis_date=analysis_date,
            exclude_st=exclude_st,
            limit=top_n,
        )
        if not records:
            return None
        scanned_count = self.market_snapshot_store.count_market_snapshot_records(
            analysis_date=analysis_date,
            exclude_st=exclude_st,
        )
        output_path = Path(output_dir)
        suffix = "_no_st" if exclude_st else ""
        return DailyPushSource(
            analysis_date=analysis_date,
            scanned_count=scanned_count or len(records),
            skipped_count=0,
            report_path=output_path / f"market_top_{top_n}_{analysis_date}{suffix}.md",
            csv_path=output_path / f"market_top_{top_n}_{analysis_date}{suffix}.csv",
            table=self._build_table_from_records(records),
            data_source="mongo",
        )

    def _load_candidate_pool_context(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        top_k: int,
    ) -> CandidatePoolPushContext:
        if self.candidate_pool_store is None:
            return CandidatePoolPushContext(states=[], events=[], note="候选池：未配置 Mongo，跳过状态日报。")
        if not hasattr(self.candidate_pool_store, "find_candidate_pool_states") or not hasattr(self.candidate_pool_store, "find_candidate_pool_events"):
            return CandidatePoolPushContext(states=[], events=[], note="候选池：当前数据源不支持候选池读取，跳过状态日报。")
        try:
            states = self.candidate_pool_store.find_candidate_pool_states(
                analysis_date=analysis_date,
                exclude_st=exclude_st,
                limit=max(1, int(top_k)) * 8,
            )
            events = self.candidate_pool_store.find_candidate_pool_events(
                analysis_date=analysis_date,
                exclude_st=exclude_st,
                limit=max(1, int(top_k)) * 8,
            )
        except Exception as error:
            return CandidatePoolPushContext(states=[], events=[], note=f"候选池：读取失败，已跳过状态日报（{error}）。")
        if not states and not events:
            return CandidatePoolPushContext(
                states=[],
                events=[],
                note=f"候选池：{analysis_date} 尚未生成状态，请先运行 `shilun-candidate-pools --date {analysis_date}`。",
            )
        return CandidatePoolPushContext(states=states, events=events)

    @staticmethod
    def _source_from_snapshot_result(snapshot_result: SnapshotJobResult, *, data_source: str) -> DailyPushSource:
        return DailyPushSource(
            analysis_date=snapshot_result.analysis_date,
            scanned_count=snapshot_result.scanned_count,
            skipped_count=snapshot_result.skipped_count,
            report_path=snapshot_result.report_path,
            csv_path=snapshot_result.csv_path,
            table=pd.read_csv(snapshot_result.csv_path),
            data_source=data_source,
        )

    @staticmethod
    def _build_table_from_records(records: list[dict[str, Any]]) -> pd.DataFrame:
        frame = pd.DataFrame(records)
        defaults: dict[str, Any] = {
            "ticker": "",
            "name": "",
            "industry": "",
            "market": "",
            "close": 0.0,
            "conclusion_label": "",
            "action_label": "",
            "trend_stage": "",
            "candidate_tags": "",
            "strategy_ids": "",
            "execution_score": 0.0,
            "target_position_pct": 0,
            "opportunity_type": "",
            "entry_zone": "",
            "entry_probability": 0.0,
            "p_acceptance_1d": 0.0,
            "p_fail_fast_3d": 0.0,
            "early_stage_score": 0.0,
            "mid_stage_score": 0.0,
            "late_stage_score": 0.0,
            "market_trend_score": 0.0,
            "sector_trend_score": 0.0,
            "fundamental_score": 0.0,
            "structure_score": 0,
            "p_continue_10d": 0.0,
            "p_breakout_success": 0.0,
            "expected_return_10d": 0.0,
            "risk_score": 0,
            "risk_level": 0.0,
        }
        for column, default in defaults.items():
            if column not in frame.columns:
                frame[column] = default
        return build_output_table(frame)

    def _build_mongo_store(self) -> MongoSnapshotStore | None:
        if not self.config.mongo_uri:
            return None
        return MongoSnapshotStore(self.config.mongo_uri, self.config.mongo_db)

    def _find_latest_local_snapshot(self, output_dir: str, *, exclude_st: bool) -> SnapshotJobResult | None:
        output_path = Path(output_dir)
        suffix = "_no_st" if exclude_st else ""
        candidates = sorted(output_path.glob(f"market_top_*_*{suffix}.csv"))
        parsed: list[tuple[str, Path]] = []
        for path in candidates:
            date_text = self._extract_date_from_snapshot_name(path.name)
            if date_text:
                parsed.append((date_text, path))
        if not parsed:
            return None
        analysis_date, csv_path = max(parsed, key=lambda item: item[0])
        report_path = csv_path.with_suffix(".md")
        return SnapshotJobResult(
            analysis_date=analysis_date,
            scanned_count=0,
            skipped_count=0,
            report_path=report_path,
            csv_path=csv_path,
            history_cache_path=output_path / f"market_history_{analysis_date}.pkl",
        )

    @staticmethod
    def _extract_date_from_snapshot_name(name: str) -> str | None:
        stem = Path(name).stem
        for part in stem.split("_"):
            try:
                datetime.strptime(part, "%Y-%m-%d")
                return part
            except ValueError:
                continue
        return None

    def _build_telegram_client(self) -> TelegramBotClient | None:
        if not self.config.telegram_bot_token:
            return None
        return TelegramBotClient(
            bot_token=self.config.telegram_bot_token,
            api_base=self.config.telegram_api_base,
            timeout=self.config.tushare_timeout,
        )

    def _build_feishu_client(self) -> FeishuBotClient | None:
        if not self.config.feishu_webhook_url:
            return None
        return FeishuBotClient(self.config.feishu_webhook_url, timeout=self.config.tushare_timeout)

    def _resolve_telegram_chat_ids(self) -> tuple[int, ...]:
        # Feishu-first policy: Telegram daily push is opt-in and must use explicit push chat ids.
        if self.config.telegram_push_chat_ids:
            return self.config.telegram_push_chat_ids
        return ()

    @classmethod
    def _build_message_text(
        cls,
        *,
        analysis_date: str,
        scanned_count: int,
        skipped_count: int,
        table: pd.DataFrame,
        top_k: int,
        candidate_pool_context: CandidatePoolPushContext | None = None,
        candidate_pool_top_k: int = 10,
        max_chars: int,
    ) -> str:
        if table.empty:
            raise ValueError("Snapshot table is empty.")
        top_rows = table.head(max(1, int(top_k)))
        lines = [
            f"石论日报 {analysis_date}",
            f"扫描 {scanned_count or len(table)} 只，跳过 {skipped_count} 只。",
        ]
        candidate_pool_lines = cls._build_candidate_pool_lines(
            candidate_pool_context,
            top_k=max(1, int(candidate_pool_top_k)),
        )
        if candidate_pool_lines:
            lines.extend(["", *candidate_pool_lines, ""])
        lines.append(f"Top{len(top_rows)}：")
        truncated = False
        appended_rows = 0
        for index, row in enumerate(top_rows.to_dict(orient="records"), start=1):
            candidate_line = cls._format_row(row)
            candidate_lines = lines + [candidate_line]
            if max_chars > 0 and len("\n".join(candidate_lines)) > max_chars:
                truncated = index > 1 or len(top_rows) > 1
                break
            lines.append(candidate_line)
            appended_rows += 1
        if truncated:
            remaining = max(0, len(top_rows) - appended_rows)
            if remaining > 0:
                lines.append(f"... 其余 {remaining} 条已省略，请查看导出的 CSV/Markdown 明细。")
        return "\n".join(lines)

    @classmethod
    def _build_candidate_pool_lines(
        cls,
        context: CandidatePoolPushContext | None,
        *,
        top_k: int,
    ) -> list[str]:
        if context is None:
            return []
        if context.note:
            return [context.note]

        lines = ["候选池状态："]
        event_groups = [
            ("promoted", "升池"),
            ("reentered", "重新进入"),
            ("entered", "新进入"),
            ("rejected", "淘汰"),
            ("demoted", "降级"),
        ]
        for event_type, label in event_groups:
            events = cls._filter_sorted(context.events, event_type=event_type)[:top_k]
            if events:
                lines.append(f"{label}：")
                lines.extend(cls._format_candidate_pool_item(event) for event in events)

        buy_states = cls._filter_sorted(context.states, pool_status="buy_pool")[:top_k]
        watch_states = cls._filter_sorted(context.states, pool_status="watch_pool")[:top_k]
        if buy_states:
            lines.append(f"买入池 Top{len(buy_states)}：")
            lines.extend(cls._format_candidate_pool_item(state) for state in buy_states)
        if watch_states:
            lines.append(f"观察池 Top{len(watch_states)}：")
            lines.extend(cls._format_candidate_pool_item(state) for state in watch_states)
        if len(lines) == 1:
            lines.append("今日无明显候选池状态变化。")
        return lines

    @staticmethod
    def _filter_sorted(
        rows: list[dict[str, Any]],
        *,
        event_type: str | None = None,
        pool_status: str | None = None,
    ) -> list[dict[str, Any]]:
        filtered = rows
        if event_type is not None:
            filtered = [row for row in filtered if row.get("event_type") == event_type]
        if pool_status is not None:
            filtered = [row for row in filtered if row.get("pool_status") == pool_status]
        return sorted(filtered, key=lambda row: int(row.get("rank") or 999999))

    @staticmethod
    def _format_candidate_pool_item(row: dict[str, Any]) -> str:
        ticker = row.get("ticker", "-")
        name = row.get("name", "-")
        rank = row.get("rank", "-")
        pool_status = row.get("pool_status", "-")
        score = row.get("pool_score", "-")
        reasons = str(row.get("pool_reasons") or "").strip()
        reason_text = f" | {reasons}" if reasons else ""
        return f"- {ticker} {name} | {pool_status} | rank {rank} | score {score}{reason_text}"

    @staticmethod
    def _format_row(row: dict[str, Any]) -> str:
        rank = row.get("排名", "-")
        ticker = row.get("股票代码", "-")
        name = row.get("股票名称", "-")
        score = row.get("执行分", "-")
        action = row.get("动作标签", "-")
        entry = row.get("入场概率", "-")
        expected = row.get("10日预期收益", "-")
        return f"{rank}. {ticker} {name} | 执行分 {score} | 动作 {action} | 入场 {entry} | 10日预期 {expected}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Shilun daily snapshot and push the Top-N list to Feishu and Telegram.")
    parser.add_argument("--date", help="Analysis date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--top-n", type=int, default=500, help="How many rows to export into snapshot outputs.")
    parser.add_argument("--message-top-k", type=int, default=20, help="How many top rows to include in the pushed message.")
    parser.add_argument("--candidate-pool-top-k", type=int, default=10, help="How many candidate pool rows to include per section.")
    parser.add_argument(
        "--max-message-chars",
        type=int,
        default=3500,
        help="Hard cap for pushed message length. Use 0 to disable truncation.",
    )
    parser.add_argument("--output-dir", default="outputs", help="Directory for CSV/Markdown outputs.")
    parser.add_argument("--include-st", action="store_true", help="Include ST and *ST stocks in the universe.")
    parser.add_argument("--dry-run", action="store_true", help="Build the message only and print it without sending.")
    parser.add_argument(
        "--disable-latest-local-fallback",
        action="store_true",
        help="Do not reuse the newest local snapshot when today's Mongo records are unavailable.",
    )
    parser.add_argument(
        "--allow-snapshot-fallback",
        action="store_true",
        help="Allow daily push to run SnapshotJob if Mongo records are missing. This may call Tushare.",
    )
    parser.add_argument(
        "--no-candidate-pool",
        action="store_true",
        help="Do not include candidate pool states/events in the pushed message.",
    )
    args = parser.parse_args()

    job = DailyPushJob()
    result = job.run(
        DailyPushRequest(
            target_date=args.date,
            top_n=args.top_n,
            message_top_k=args.message_top_k,
            candidate_pool_top_k=args.candidate_pool_top_k,
            max_message_chars=args.max_message_chars,
            output_dir=args.output_dir,
            exclude_st=not args.include_st,
            dry_run=args.dry_run,
            fallback_latest_local=not args.disable_latest_local_fallback,
            allow_snapshot_fallback=args.allow_snapshot_fallback,
            include_candidate_pool=not args.no_candidate_pool,
        )
    )
    print(f"analysis_date={result.analysis_date}")
    print(f"csv_path={result.csv_path}")
    print(f"report_path={result.report_path}")
    print(f"pushed_channels={','.join(result.pushed_channels)}")
    print(f"failed_channels={','.join(result.failed_channels)}")
    print(f"data_source={result.data_source}")
    print("message_text:")
    print(result.message_text)


if __name__ == "__main__":
    main()
