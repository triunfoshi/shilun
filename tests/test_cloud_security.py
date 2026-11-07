import base64
import unittest
from types import SimpleNamespace

from fastapi import FastAPI

from shilun.api.security import _credentials, install_basic_auth


class CloudSecurityTests(unittest.TestCase):
    def test_basic_credentials_parser(self) -> None:
        encoded = base64.b64encode("trader:secret".encode()).decode()
        self.assertEqual(("trader", "secret"), _credentials(f"Basic {encoded}"))
        self.assertIsNone(_credentials("Bearer token"))

    def test_enabled_auth_requires_both_secrets(self) -> None:
        app = FastAPI()
        config = SimpleNamespace(auth_required=True, auth_username="trader", auth_password=None)
        with self.assertRaises(RuntimeError):
            install_basic_auth(app, config)

    def test_disabled_auth_does_not_install_middleware(self) -> None:
        app = FastAPI()
        config = SimpleNamespace(auth_required=False, auth_username=None, auth_password=None)
        install_basic_auth(app, config)
        self.assertEqual([], app.user_middleware)


if __name__ == "__main__":
    unittest.main()
