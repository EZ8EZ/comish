"""BlueBubbles webhook payloads shaped like BlueBubbles Server v1.9.x output."""

GROUP_GUID = "iMessage;+;chat600000000000000001"
OTHER_GROUP_GUID = "iMessage;+;chat600000000000000002"


def new_message(
    text: str | None = "@comish ping",
    guid: str = "msg-1",
    chat_guid: str = GROUP_GUID,
    sender: str = "+15555550101",
    **overrides,
) -> dict:
    data = {
        "originalROWID": 101,
        "guid": guid,
        "text": text,
        "handle": {"originalROWID": 3, "address": sender, "service": "iMessage"},
        "handleId": 3,
        "attachments": [],
        "dateCreated": 1_780_000_000_000,
        "isFromMe": False,
        "itemType": 0,
        "groupActionType": 0,
        "associatedMessageGuid": None,
        "associatedMessageType": None,
        "dateEdited": None,
        "dateRetracted": None,
        "chats": [{"guid": chat_guid, "style": 43, "displayName": "Dynasty FB test"}],
    }
    data.update(overrides)
    return {"type": "new-message", "data": data}
