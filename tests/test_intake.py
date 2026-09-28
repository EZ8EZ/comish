import pytest

from comish import intake
from comish.transport.bluebubbles import parse_webhook
from tests.fixtures import GROUP_GUID, OTHER_GROUP_GUID, new_message

ALLOWED = frozenset({GROUP_GUID})


@pytest.mark.parametrize(
    "text",
    [
        "@comish ping",
        "@Comish what's the taxi deadline?",
        "hey @COMISH, quick one",
        "question for @comish?",
        "(@comish) trade deadline",
        "@comish\nmultiline",
        "@commish ping",
        "@Commish what is the waiver type?",
    ],
)
def test_mention_matches(text):
    assert intake.is_mention(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "comish ping",
        "@comishbot ping",
        "@comish_ ping",
        "@commishbot ping",
        "@comiish ping",
        "bot@commish.com",
        "email me at bot@comish.com",
        "ask the comish",
        "@ comish",
    ],
)
def test_mention_rejects(text):
    assert not intake.is_mention(text)


def test_strip_mention():
    assert intake.strip_mention("@comish  what is the  trade deadline?") == (
        "what is the trade deadline?"
    )
    assert intake.strip_mention("@Commish taxi rules?") == "taxi rules?"


def _evaluate(payload, seen=None):
    return intake.evaluate(parse_webhook(payload), ALLOWED, seen or intake.SeenMessages())


def test_handles_mention_in_bound_group():
    decision = _evaluate(new_message("@comish ping"))
    assert decision.handle
    assert decision.question == "ping"


@pytest.mark.parametrize(
    "payload,reason",
    [
        (new_message("@comish ping", isFromMe=True), "from_me"),
        (new_message("@comish ping", chat_guid=OTHER_GROUP_GUID), "unbound_chat"),
        (new_message("@comish ping", associatedMessageType=2000), "reaction"),
        (new_message("@comish ping", associatedMessageType="love"), "reaction"),
        (new_message("@comish ping", dateRetracted=1_780_000_000_500), "retracted"),
        (new_message("@comish ping", itemType=1), "system"),
        (new_message("hello league"), "no_mention"),
        (new_message(None, attachments=[{"guid": "att-1"}]), "no_mention"),
    ],
)
def test_ignores(payload, reason):
    decision = _evaluate(payload)
    assert not decision.handle
    assert decision.reason == reason


def test_bot_reply_containing_mention_never_loops():
    decision = _evaluate(new_message("pong, ask @comish anytime", isFromMe=True))
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
