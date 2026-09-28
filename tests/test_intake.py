import pytest

from commish import intake
from commish.transport.bluebubbles import parse_webhook
from tests.fixtures import GROUP_GUID, OTHER_GROUP_GUID, new_message

ALLOWED = frozenset({GROUP_GUID})


@pytest.mark.parametrize(
    "text",
    [
        "@commish ping",
        "@Commish what's the taxi deadline?",
        "hey @COMMISH, quick one",
        "question for @commish?",
        "(@commish) trade deadline",
        "@commish\nmultiline",
    ],
)
def test_mention_matches(text):
    assert intake.is_mention(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "commish ping",
        "@commishbot ping",
        "@commish_ ping",
        "email me at bot@commish.com",
        "ask the commish",
        "@ commish",
    ],
)
def test_mention_rejects(text):
    assert not intake.is_mention(text)


def test_strip_mention():
    assert intake.strip_mention("@commish  what is the  trade deadline?") == (
        "what is the trade deadline?"
    )


def _evaluate(payload, seen=None):
    return intake.evaluate(parse_webhook(payload), ALLOWED, seen or intake.SeenMessages())


def test_handles_mention_in_bound_group():
    decision = _evaluate(new_message("@commish ping"))
    assert decision.handle
    assert decision.question == "ping"


@pytest.mark.parametrize(
    "payload,reason",
    [
        (new_message("@commish ping", isFromMe=True), "from_me"),
        (new_message("@commish ping", chat_guid=OTHER_GROUP_GUID), "unbound_chat"),
        (new_message("@commish ping", associatedMessageType=2000), "reaction"),
        (new_message("@commish ping", associatedMessageType="love"), "reaction"),
        (new_message("@commish ping", dateRetracted=1_780_000_000_500), "retracted"),
        (new_message("@commish ping", itemType=1), "system"),
        (new_message("hello league"), "no_mention"),
        (new_message(None, attachments=[{"guid": "att-1"}]), "no_mention"),
    ],
)
def test_ignores(payload, reason):
    decision = _evaluate(payload)
    assert not decision.handle
    assert decision.reason == reason


def test_bot_reply_containing_mention_never_loops():
    decision = _evaluate(new_message("pong — ask @commish anytime", isFromMe=True))
    assert decision.reason == "from_me"


def test_duplicate_delivery_handled_once():
    seen = intake.SeenMessages()
    assert _evaluate(new_message(guid="dup"), seen).handle
    assert _evaluate(new_message(guid="dup"), seen).reason == "duplicate"


def test_seen_messages_is_bounded():
    seen = intake.SeenMessages(maxlen=2)
    for guid in ("a", "b", "c"):
        assert seen.add(guid)
    assert seen.add("a")  # evicted, so treated as new
    assert not seen.add("c")
