from __future__ import annotations

from typing import Any

import requests


class FeishuBotClient:
    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send_text(self, text: str) -> dict[str, Any]:
        response = requests.post(
            self.webhook_url,
            json={
                "msg_type": "text",
                "content": {
                    "text": text,
                },
            },
            timeout=self.timeout,
        )
        return self._handle_response(response)

    @staticmethod
    def _handle_response(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(f"Feishu API returned non-JSON response: {response.text}") from error
        if not response.ok or int(payload.get("code", 0)) != 0:
            raise RuntimeError(str(payload.get("msg") or f"Feishu API request failed with status {response.status_code}"))
        return payload

__all__ = ["FeishuBotClient"]
