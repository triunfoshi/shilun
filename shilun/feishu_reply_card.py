"""Feishu Card 2.0 reply with a quiet, compact usage caption."""

from __future__ import annotations

from html import escape
from typing import Any


def reply_card(body: str, usage: str) -> dict[str, Any]:
    """Keep the answer prominent and the two usage lines in 12px grey text.

    Only our caption adds card HTML. Model-authored HTML is escaped so an
    answer cannot introduce mention tags while retaining ordinary Markdown.
    """
    return {
        "schema": "2.0",
        "config": {
            "width_mode": "default",
            "style": {"text_size": {
                "reply": {"default": "normal", "pc": "normal", "mobile": "normal"},
                "usage": {"default": "notation", "pc": "notation", "mobile": "notation"},
            }},
        },
        "body": {
            "direction": "vertical", "padding": "12px", "vertical_spacing": "8px",
            "elements": [
                {"tag": "markdown", "content": escape(body, quote=False), "text_size": "reply"},
                {"tag": "column_set", "flex_mode": "none", "columns": [
                    {"tag": "column", "width": "weighted", "weight": 1,
                     "background_style": "grey-50", "padding": "8px", "elements": [
                         {"tag": "markdown", "text_size": "usage",
                          "content": "<font color='grey'>" + escape(usage, quote=False) + "</font>"},
                     ]},
                ]},
            ],
        },
    }
