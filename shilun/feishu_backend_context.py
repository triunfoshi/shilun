"""Route Feishu requests to the system's supported read-only data adapters."""

from shilun.feishu_board_effects_context import build_board_effects_context
from shilun.feishu_review_context import ReviewContext, build_review_context


def build_backend_context(message: str, *, max_chars: int = 24000) -> ReviewContext | None:
    # A statistics request can also contain the word "复盘" and an explicit
    # date. Select its dedicated window before the single-day dashboard route.
    context = build_board_effects_context(message, max_chars=max_chars)
    if context is not None:
        return context
    return build_review_context(message, max_chars=max_chars)
