"""路由层回归测试：不依赖真实通道、不调用真实模型。"""

from __future__ import annotations

import unittest

import app.router as router
from app.channels.base import Channel, IncomingMessage


class FakeChannel(Channel):
    name = "fake"

    def __init__(self) -> None:
        self.typing: list[bool] = []
        self.streams: list[str] = []
        self.texts: list[str] = []

    async def start(self, on_message) -> None:
        return None

    async def reply_text(self, message: IncomingMessage, text: str) -> None:
        self.texts.append(text)

    async def reply_stream(self, message: IncomingMessage, chunks) -> str:
        answer = ""
        async for delta in chunks:
            answer += delta
        self.streams.append(answer)
        return answer

    async def send_typing(self, message: IncomingMessage, on: bool = True) -> None:
        self.typing.append(on)


class RouterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._original_stream = router.astream_answer

        async def fake_stream(question: str):
            yield "hello"
            yield " world"

        router.astream_answer = fake_stream

    async def asyncTearDown(self) -> None:
        router.astream_answer = self._original_stream

    @staticmethod
    def _message(message_id: str) -> IncomingMessage:
        return IncomingMessage(
            channel="fake", message_id=message_id, user_id="user-1", text="hi"
        )

    async def test_handle_message_streams_reply_and_typing(self) -> None:
        channel = FakeChannel()
        await router.handle_message(channel, self._message("1"))

        self.assertEqual(channel.streams, ["hello world"])
        self.assertEqual(channel.typing, [True, False])
        self.assertEqual(channel.texts, [])

    async def test_run_channel_binds_single_argument_handler(self) -> None:
        channel = FakeChannel()
        captured = {}

        async def fake_start(on_message) -> None:
            captured["handler"] = on_message
            await on_message(self._message("2"))

        channel.start = fake_start
        original_create = router.create_channel
        router.create_channel = lambda name: channel
        try:
            await router.run_channel("fake")
        finally:
            router.create_channel = original_create

        self.assertIn("handler", captured)
        self.assertEqual(channel.streams, ["hello world"])


if __name__ == "__main__":
    unittest.main()
