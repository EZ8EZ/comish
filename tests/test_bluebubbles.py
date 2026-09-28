import asyncio
import json

import httpx
import pytest

from commish.transport.bluebubbles import (
    BlueBubblesError,
    BlueBubblesTransport,
    is_group_chat,
    parse_webhook,
)
from tests.fixtures import GROUP_GUID, new_message


def test_parse_group_message():
    msg = parse_webhook(new_message("@commish ping", sender="+15555550199"))
    assert msg.guid == "msg-1"
    assert msg.chat_guid == GROUP_GUID
    assert msg.is_group
    assert msg.sender == "+15555550199"
    assert msg.text == "@commish ping"
    assert msg.sent_at_ms == 1_780_000_000_000
    assert not (msg.is_from_me or msg.is_reaction or msg.is_retracted or msg.is_system)


def test_parse_ignores_other_events():
    assert parse_webhook({"type": "updated-message", "data": new_message()["data"]}) is None
    assert parse_webhook({"type": "typing-indicator", "data": {}}) is None
    assert parse_webhook({}) is None


def test_parse_requires_chat():
    assert parse_webhook(new_message(chats=[])) is None


def test_own_message_has_no_sender():
    assert parse_webhook(new_message(isFromMe=True)).sender is None


@pytest.mark.parametrize(
    "chat,expected",
    [
        ({"guid": "iMessage;+;chat123", "style": 43}, True),
        ({"guid": "any;+;chat123"}, True),
        ({"guid": "iMessage;-;+15555550101", "style": 45}, False),
        ({"guid": "any;-;commish.bot@example.com"}, False),
    ],
)
def test_is_group_chat(chat, expected):
    assert is_group_chat(chat) is expected


def _transport(handler) -> BlueBubblesTransport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return BlueBubblesTransport("http://127.0.0.1:1234/", "s3cret", "apple-script", client)


async def _send(transport):
    await transport.send_text(GROUP_GUID, "pong")


def test_send_text_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": 200, "message": "Success", "data": {}})

    asyncio.run(_send(_transport(handler)))
    assert seen["url"].path == "/api/v1/message/text"
    assert seen["url"].params["password"] == "s3cret"
    assert seen["body"]["chatGuid"] == GROUP_GUID
    assert seen["body"]["message"] == "pong"
    assert seen["body"]["method"] == "apple-script"
    assert seen["body"]["tempGuid"].startswith("commish-")


def test_send_text_raises_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"status": 500, "message": "Error", "error": {"type": "x", "error": "boom"}},
        )

    with pytest.raises(BlueBubblesError, match="500"):
        asyncio.run(_send(_transport(handler)))
