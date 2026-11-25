from __future__ import annotations

import pytest

from tervic.core.redact import REDACTED_RE, Redactor, find_secrets, is_placeholder, redact_text, redacted_kinds

SECRETS = {
    "api_key": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234",
    "aws_key": "AKIAIOSFODNN7EXAMPLQ",
    "github_token": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "slack_token": "xoxb-1234567890-abcdefghij",
    "google_key": "AIza" + "SyA1234567890abcdefghijklmnopqrstuv",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
}


@pytest.mark.parametrize(("kind", "secret"), list(SECRETS.items()))
def test_known_secret_formats_are_found_and_redacted(kind: str, secret: str) -> None:
    text = f"config value {secret} end"
    assert (kind, secret) in find_secrets(text)
    redacted = redact_text(text)
    assert secret not in redacted
    assert REDACTED_RE.search(redacted)


def test_private_key_bearer_connection_string_and_kv() -> None:
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----\n"
        "Authorization: Bearer abcdef1234567890ABCDEF\n"
        "postgres://admin:hunter2pass@db.internal:5432/app\n"
        "password=correcthorse"
    )
    kinds = {kind for kind, _ in find_secrets(text)}
    assert {"private_key", "bearer", "connection_string", "kv_secret"} <= kinds
    out = redact_text(text)
    for leaked in ("MIIEow", "abcdef1234567890ABCDEF", "hunter2pass", "correcthorse"):
        assert leaked not in out


def test_high_entropy_tokens_but_not_hex_hashes_or_ulids() -> None:
    token = "q8Zr2LmN4pXv7TbW1cY9kHs3dFg6JeRa5Uo0"
    assert ("high_entropy", token) in find_secrets(f"token {token}")
    sha = "a" * 10 + "0123456789abcdef" * 3
    assert not find_secrets(f"digest {sha}")
    assert not find_secrets("run 01M7VXQ9DYF0KE20NQJXFTS4YQ")


@pytest.mark.parametrize("placeholder", [
    "YOUR_API_KEY", "your-key-here", "<token>", "${OPENAI_API_KEY}", "xxxxxxxx", "changeme", "example-key", "***",
])
def test_placeholders_are_not_secrets(placeholder: str) -> None:
    assert is_placeholder(placeholder)
    assert not find_secrets(f"api_key={placeholder}")
    assert Redactor()({"api_key": placeholder}) == {"api_key": placeholder}


def test_equal_secrets_redact_to_equal_tags() -> None:
    a = redact_text("key sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234")
    b = redact_text("other sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234")
    assert REDACTED_RE.search(a).group(0) == REDACTED_RE.search(b).group(0)  # type: ignore[union-attr]


def test_redactor_walks_structures_and_sensitive_keys() -> None:
    redactor = Redactor()
    data = {"user": "ada", "api_key": "plain-looking-value", "nested": [{"note": "sk-ant-abcdefghijklmnopqrstuvwxyz"}]}
    out = redactor(data)
    assert out["user"] == "ada"
    assert out["api_key"].startswith("[REDACTED:kv_secret:")
    assert out["nested"][0]["note"].startswith("[REDACTED:api_key:")
    assert redacted_kinds(str(out)) == ["kv_secret", "api_key"]


def test_pii_only_when_enabled() -> None:
    text = "mail ada@example.org, call +1 415 555 0100, card 4111 1111 1111 1111"
    assert not [k for k, _ in find_secrets(text)]
    kinds = {k for k, _ in find_secrets(text, pii=True)}
    assert {"email", "phone", "card"} <= kinds
    assert "ada@example.org" not in Redactor(pii=True)(text)
    assert "4111 1111 1111 1112" not in [v for _, v in find_secrets("card 4111 1111 1111 1112", pii=True)]


def test_disabled_redaction_and_user_hook() -> None:
    assert Redactor(enabled=False)("sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234").startswith("sk-proj")
    hooked = Redactor(hook=lambda value: "HOOKED")
    assert hooked({"a": 1}) == "HOOKED"
