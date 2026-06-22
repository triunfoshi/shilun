from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime

from shilun.api import TelegramBotClient
from shilun.common.config import AppConfig, load_config
from shilun.jobs.daily_push_job import DailyPushJob, DailyPushRequest, DailyPushResult


@dataclass(frozen=True)
class AutomationPushRequest:
    target_date: str | None = None
    dry_run: bool = False
    top_n: int = 500
    message_top_k: int = 20
    candidate_pool_top_k: int = 10
    max_message_chars: int = 3500
    output_dir: str = "outputs"
    include_st: bool = False
    include_candidate_pool: bool = True
    allow_snapshot_fallback: bool = True
    fallback_latest_local: bool = False
    prefer_mongo: bool = True
    telegram_discovery_limit: int = 20
    require_feishu: bool = True
    require_telegram: bool = True


@dataclass(frozen=True)
class AutomationPushResult:
    push_result: DailyPushResult
    telegram_chat_ids: tuple[int, ...]
    telegram_chat_ids_source: str


def discover_telegram_chat_ids(client: TelegramBotClient, *, limit: int = 20) -> tuple[int, ...]:
    payload = client.get_updates(limit=max(1, int(limit)), timeout=0)
    results = payload.get("result") or []
    ordered_ids: list[int] = []
    seen: set[int] = set()
    for update in reversed(results):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int) or chat_id in seen:
            continue
        ordered_ids.append(chat_id)
        seen.add(chat_id)
    return tuple(ordered_ids)


class AutomationPushJob:
    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        telegram_client: TelegramBotClient | None = None,
    ) -> None:
        self.config = config or load_config()
        self.telegram_client = telegram_client or self._build_telegram_client()

    def run(self, request: AutomationPushRequest) -> AutomationPushResult:
        telegram_chat_ids, telegram_chat_ids_source = self._resolve_telegram_chat_ids(
            limit=request.telegram_discovery_limit
        )

        config = self.config
        if telegram_chat_ids:
            config = replace(config, telegram_push_chat_ids=telegram_chat_ids)
        push_job = self._build_push_job(config=config, request=request)
        push_result = push_job.run(
            DailyPushRequest(
                target_date=request.target_date,
                top_n=request.top_n,
                message_top_k=request.message_top_k,
                candidate_pool_top_k=request.candidate_pool_top_k,
                max_message_chars=request.max_message_chars,
                output_dir=request.output_dir,
                exclude_st=not request.include_st,
                dry_run=request.dry_run,
                fallback_latest_local=request.fallback_latest_local,
                prefer_mongo=request.prefer_mongo,
                allow_snapshot_fallback=request.allow_snapshot_fallback,
                include_candidate_pool=request.include_candidate_pool,
            )
        )
        self._validate_required_channels(
            push_result=push_result,
            telegram_chat_ids=telegram_chat_ids,
            telegram_chat_ids_source=telegram_chat_ids_source,
            request=request,
        )
        return AutomationPushResult(
            push_result=push_result,
            telegram_chat_ids=telegram_chat_ids,
            telegram_chat_ids_source=telegram_chat_ids_source,
        )

    def _build_telegram_client(self) -> TelegramBotClient | None:
        if not self.config.telegram_bot_token:
            return None
        return TelegramBotClient(
            bot_token=self.config.telegram_bot_token,
            api_base=self.config.telegram_api_base,
            timeout=self.config.tushare_timeout,
        )

    def _resolve_telegram_chat_ids(self, *, limit: int) -> tuple[tuple[int, ...], str]:
        if self.config.telegram_push_chat_ids:
            return self.config.telegram_push_chat_ids, "config"
        if self.telegram_client is None:
            return (), "unavailable"
        try:
            discovered = discover_telegram_chat_ids(self.telegram_client, limit=limit)
        except Exception:
            return (), "discovery_failed"
        if discovered:
            return discovered, "discovered"
        return (), "discovery_empty"

    def _build_push_job(self, *, config: AppConfig, request: AutomationPushRequest) -> DailyPushJob:
        try:
            return DailyPushJob(
                config=config,
                telegram_client=self.telegram_client,
            )
        except Exception:
            if not request.prefer_mongo or not config.mongo_uri:
                raise
            fallback_config = replace(config, mongo_uri=None)
            return DailyPushJob(
                config=fallback_config,
                telegram_client=self.telegram_client,
            )

    @staticmethod
    def _validate_required_channels(
        *,
        push_result: DailyPushResult,
        telegram_chat_ids: tuple[int, ...],
        telegram_chat_ids_source: str,
        request: AutomationPushRequest,
    ) -> None:
        if request.dry_run:
            return

        pushed = set(push_result.pushed_channels)
        failed = " | ".join(push_result.failed_channels)

        if request.require_feishu and "feishu" not in pushed:
            raise RuntimeError(
                "Automation push requires Feishu delivery, but Feishu did not succeed."
                + (f" Failures: {failed}" if failed else "")
            )

        telegram_pushed = any(channel.startswith("telegram:") for channel in pushed)
        if request.require_telegram and not telegram_pushed:
            if not telegram_chat_ids:
                raise RuntimeError(
                    "Automation push requires Telegram delivery, but no Telegram chat id was available "
                    f"(source={telegram_chat_ids_source}). Configure SHILUN_TELEGRAM_PUSH_CHAT_IDS or message the bot first."
                )
            raise RuntimeError(
                "Automation push requires Telegram delivery, but Telegram did not succeed."
                + (f" Failures: {failed}" if failed else "")
            )


def _today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automation-oriented Shilun daily push. Defaults to Mongo-first execution, then Tushare snapshot fallback, with Telegram chat discovery."
    )
    parser.add_argument("--date", default=_today_text(), help="Target date in YYYY-MM-DD format. Defaults to today.")
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
        "--snapshot-first",
        action="store_true",
        help="Skip Mongo-first lookup and execute snapshot generation directly.",
    )
    parser.add_argument(
        "--allow-stale-local-fallback",
        action="store_true",
        help="Allow reusing the newest local snapshot when the requested date cannot be built. Disabled by default for automation.",
    )
    parser.add_argument(
        "--disable-snapshot-fallback",
        action="store_true",
        help="Do not allow snapshot execution when the preferred data source is unavailable.",
    )
    parser.add_argument(
        "--no-candidate-pool",
        action="store_true",
        help="Do not include candidate pool states/events in the pushed message.",
    )
    parser.add_argument(
        "--telegram-discovery-limit",
        type=int,
        default=20,
        help="How many recent Telegram updates to inspect when chat ids are not configured.",
    )
    parser.add_argument(
        "--allow-feishu-optional",
        action="store_true",
        help="Do not fail the automation when Feishu delivery is unavailable.",
    )
    parser.add_argument(
        "--allow-telegram-optional",
        action="store_true",
        help="Do not fail the automation when Telegram delivery is unavailable.",
    )
    args = parser.parse_args()

    result = AutomationPushJob().run(
        AutomationPushRequest(
            target_date=args.date,
            dry_run=args.dry_run,
            top_n=args.top_n,
            message_top_k=args.message_top_k,
            candidate_pool_top_k=args.candidate_pool_top_k,
            max_message_chars=args.max_message_chars,
            output_dir=args.output_dir,
            include_st=args.include_st,
            include_candidate_pool=not args.no_candidate_pool,
            allow_snapshot_fallback=not args.disable_snapshot_fallback,
            fallback_latest_local=args.allow_stale_local_fallback,
            prefer_mongo=not args.snapshot_first,
            telegram_discovery_limit=args.telegram_discovery_limit,
            require_feishu=not args.allow_feishu_optional,
            require_telegram=not args.allow_telegram_optional,
        )
    )
    push_result = result.push_result
    print(f"target_date={args.date}")
    print(f"analysis_date={push_result.analysis_date}")
    print(f"csv_path={push_result.csv_path}")
    print(f"report_path={push_result.report_path}")
    print(f"pushed_channels={','.join(push_result.pushed_channels)}")
    print(f"failed_channels={','.join(push_result.failed_channels)}")
    print(f"data_source={push_result.data_source}")
    print(f"telegram_chat_ids_source={result.telegram_chat_ids_source}")
    print(f"telegram_chat_ids={','.join(str(value) for value in result.telegram_chat_ids)}")
    print("message_text:")
    print(push_result.message_text)


if __name__ == "__main__":
    main()
