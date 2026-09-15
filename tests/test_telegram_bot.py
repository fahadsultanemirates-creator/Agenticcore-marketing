from unittest.mock import patch

from agenticcore.approval.telegram_bot import TelegramApprovalBot


def _updates_response(callbacks):
    return {"ok": True, "result": callbacks}


def _ack_response():
    return {"ok": True, "result": True}


def make_callback(update_id, chat_id, data, callback_id="cb1"):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": callback_id,
            "data": data,
            "message": {"chat": {"id": chat_id}},
        },
    }


def test_tap_from_authorized_chat_is_yielded():
    bot = TelegramApprovalBot(token="t", default_chat_id="111")
    callbacks = [make_callback(1, 111, "approve:draft-1")]

    with patch("agenticcore.approval.telegram_bot.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.side_effect = [_updates_response(callbacks), _ack_response()]

        decisions = list(bot.poll_decisions(timeout=1))

    assert decisions == [("draft-1", "approve")]


def test_tap_from_unauthorized_chat_is_ignored():
    bot = TelegramApprovalBot(token="t", default_chat_id="111")
    callbacks = [make_callback(1, 999, "approve:draft-1")]

    with patch("agenticcore.approval.telegram_bot.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.side_effect = [_updates_response(callbacks), _ack_response()]

        decisions = list(bot.poll_decisions(timeout=1))

    assert decisions == []


def test_add_authorized_chat_extends_allowed_set():
    bot = TelegramApprovalBot(token="t", default_chat_id="111")
    bot.add_authorized_chat("222")
    callbacks = [make_callback(1, 222, "reject:draft-2")]

    with patch("agenticcore.approval.telegram_bot.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.side_effect = [_updates_response(callbacks), _ack_response()]

        decisions = list(bot.poll_decisions(timeout=1))

    assert decisions == [("draft-2", "reject")]


def test_empty_allow_list_refuses_every_tap():
    """A bot with no configured chat ids must trust nobody, not everybody."""

    bot = TelegramApprovalBot(token="t", default_chat_id=None)
    assert bot.authorized_chat_ids == set()
    callbacks = [make_callback(1, 999999, "approve:draft-1")]

    with patch("agenticcore.approval.telegram_bot.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.side_effect = [_updates_response(callbacks), _ack_response()]

        decisions = list(bot.poll_decisions(timeout=1))

    assert decisions == []
