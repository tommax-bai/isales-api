"""Tests for the cloud-edge token mint + CLI.

Spec: arch-cloud-edge-split § service-communication Scenario "gRPC 鉴权";
      A2 tasks 9.2.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from isales_common.utils.jwt import InvalidJWT, verify_jwt
from jose import JWTError
from jose import jwt as jose_jwt

from isales_api.edge_token import (
    ALGORITHM,
    AUDIENCE,
    SCOPE,
    _parse_ttl,
    mint_edge_token,
)
from scripts import mint_edge_token as cli

SECRET = "test-shared-secret"


def _decode(token: str) -> dict[str, object]:
    # Skip aud check on the round-trip helper so we can read it raw.
    return jose_jwt.decode(token, SECRET, algorithms=[ALGORITHM], audience=AUDIENCE)


def test_mint_returns_token_with_required_claims() -> None:
    token = mint_edge_token("edge-01", secret=SECRET)
    claims = _decode(token)
    assert claims["sub"] == "edge-01"
    assert claims["aud"] == AUDIENCE
    assert claims["scope"] == SCOPE
    assert "iat" in claims
    assert "exp" in claims


def test_mint_default_ttl_is_one_year() -> None:
    now = datetime(2026, 5, 15, tzinfo=UTC)
    token = mint_edge_token("edge-01", secret=SECRET, now=now)
    claims = _decode(token)
    assert claims["exp"] - claims["iat"] == 365 * 86400


def test_mint_custom_ttl() -> None:
    now = datetime(2026, 5, 15, tzinfo=UTC)
    token = mint_edge_token(
        "edge-01", ttl=timedelta(hours=24), secret=SECRET, now=now
    )
    claims = _decode(token)
    assert claims["exp"] - claims["iat"] == 86400


def test_mint_rejects_empty_device_id() -> None:
    with pytest.raises(ValueError, match="edge_device_id"):
        mint_edge_token("", secret=SECRET)


def test_decoding_with_wrong_audience_fails() -> None:
    """Engine verifier MUST require aud=cloud-edge; passing a different
    audience or none rejects a frontend JWT presented to the gRPC channel.
    """
    token = mint_edge_token("edge-7", secret=SECRET)
    with pytest.raises(JWTError):
        jose_jwt.decode(token, SECRET, algorithms=[ALGORITHM], audience="api")


def test_expired_token_rejected() -> None:
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    token = mint_edge_token(
        "edge-old", ttl=timedelta(seconds=60), secret=SECRET, now=long_ago
    )
    with pytest.raises(JWTError):
        jose_jwt.decode(token, SECRET, algorithms=[ALGORITHM], audience=AUDIENCE)


# isales-common verify_jwt is intentionally aud-agnostic (it serves
# frontend JWTs); engine ships its own verifier with the aud=cloud-edge
# check. This import stays as a guardrail against accidental shape drift.
_ = (verify_jwt, InvalidJWT)


@pytest.mark.parametrize(
    "text,expected_seconds",
    [
        ("365d", 365 * 86400),
        ("24h", 24 * 3600),
        ("30m", 30 * 60),
        ("90s", 90),
        ("3600", 3600),
    ],
)
def test_parse_ttl_accepts_known_forms(text: str, expected_seconds: int) -> None:
    assert _parse_ttl(text).total_seconds() == expected_seconds


@pytest.mark.parametrize("bad", ["", "abc", "1y", "10x"])
def test_parse_ttl_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ValueError):
        _parse_ttl(bad)


def test_cli_emits_token_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ISALES_JWT_SECRET", SECRET)
    rc = cli.main(["--device-id", "edge-42", "--ttl", "24h"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out  # token printed
    claims = _decode(out)
    assert claims["sub"] == "edge-42"


def test_cli_passes_signing_secret_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No env var; --signing-secret must work standalone.
    monkeypatch.delenv("ISALES_JWT_SECRET", raising=False)
    rc = cli.main(
        ["--device-id", "edge-9", "--ttl", "1h", "--signing-secret", "explicit-secret"]
    )
    assert rc == 0
    token = capsys.readouterr().out.strip()
    claims = jose_jwt.decode(
        token, "explicit-secret", algorithms=[ALGORITHM], audience=AUDIENCE
    )
    assert claims["sub"] == "edge-9"


def test_cli_errors_without_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ISALES_JWT_SECRET", raising=False)
    rc = cli.main(["--device-id", "edge-9"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "ISALES_JWT_SECRET" in err


def test_cli_errors_on_bad_ttl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ISALES_JWT_SECRET", SECRET)
    rc = cli.main(["--device-id", "edge-9", "--ttl", "1y"])
    assert rc == 2
    assert "ttl" in capsys.readouterr().err.lower()
