import unittest
from typing import Any, cast

from textual.widgets import Input

from acp_tui.app import ACPApp


class FakeClient:
    def __init__(self):
        self.notifications = []

    async def notify(self, method, params=None):
        self.notifications.append((method, params))

    async def close(self):
        pass


class TestableACPApp(ACPApp):
    async def _run(self) -> None:
        # Avoid opening a real WebSocket; tests set the app state directly.
        return


class CancelTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_ctrl_x_sends_session_cancel_for_in_flight_turn(self):
        app = TestableACPApp("ws://example.invalid/acp")

        async with app.run_test() as pilot:
            fake_client = FakeClient()
            cast(Any, app).client = fake_client
            app.acp_session_id = "sess-123"

            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.disabled = True
            prompt_input.placeholder = "agent is processing…"

            await pilot.press("ctrl+x")
            await pilot.pause()

            self.assertEqual(
                fake_client.notifications,
                [("session/cancel", {"sessionId": "sess-123"})],
            )
            self.assertEqual(prompt_input.placeholder, "cancelling…")

    async def test_ctrl_x_without_active_turn_logs_hint_and_does_not_notify(self):
        app = TestableACPApp("ws://example.invalid/acp")

        async with app.run_test() as pilot:
            fake_client = FakeClient()
            cast(Any, app).client = fake_client
            app.acp_session_id = "sess-123"

            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.disabled = False
            prompt_input.placeholder = "type a prompt and press enter"

            await pilot.press("ctrl+x")
            await pilot.pause()

            self.assertEqual(fake_client.notifications, [])
            self.assertEqual(prompt_input.placeholder, "type a prompt and press enter")


if __name__ == "__main__":
    unittest.main()
