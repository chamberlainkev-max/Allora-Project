import logging

from logging_config.logger import _RedactSecrets


def _filtered(msg: str, *args) -> str:
    record = logging.LogRecord("httpx2", logging.INFO, __file__, 1, msg, args, None)
    _RedactSecrets().filter(record)
    return record.getMessage()


def test_redacts_rpc_api_key():
    line = _filtered('HTTP Request: %s %s "%s"', "POST", "https://mainnet.helius-rpc.com/?api-key=abc-123", "HTTP/2 200 OK")
    assert "abc-123" not in line
    assert "api-key=<redacted>" in line


def test_redacts_telegram_bot_token():
    line = _filtered("POST https://api.telegram.org/bot123456:AAH-x_yz/sendMessage")
    assert "AAH-x_yz" not in line
    assert "/bot<redacted>/sendMessage" in line


def test_leaves_normal_lines_untouched():
    assert _filtered('{"event": "tx_submitted"}') == '{"event": "tx_submitted"}'
