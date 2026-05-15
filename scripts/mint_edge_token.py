"""CLI: mint a cloud-edge bearer token for one edge device.

Spec: arch-cloud-edge-split § service-communication Scenario "gRPC 鉴权";
      deploy/RUNBOOK-edge.md § "激活码与 Device Token".

Usage::

    isales-edge-token-mint \
        --device-id edge-01 \
        --ttl 365d \
        --signing-secret "$(grep ISALES_JWT_SECRET /etc/isales/env/api.env | cut -d= -f2)"

If ``--signing-secret`` is omitted, ``ISALES_JWT_SECRET`` is read from the
environment (so the CLI works without piping the secret on the command line
when running under systemd / sudo with the env loaded).
"""

from __future__ import annotations

import argparse
import os
import sys

from isales_api.edge_token import _parse_ttl, mint_edge_token


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isales-edge-token-mint",
        description="Mint a cloud-edge bearer JWT for one edge device.",
    )
    parser.add_argument(
        "--device-id",
        required=True,
        help="edge_device_id; lands in JWT 'sub' claim",
    )
    parser.add_argument(
        "--ttl",
        default="365d",
        help="token lifetime, e.g. 365d / 24h / 86400 (seconds). Default 365d.",
    )
    parser.add_argument(
        "--signing-secret",
        default=None,
        help="HMAC secret; defaults to $ISALES_JWT_SECRET",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        ttl = _parse_ttl(args.ttl)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    secret = args.signing_secret or os.environ.get("ISALES_JWT_SECRET")
    if not secret:
        print(
            "error: --signing-secret not provided and ISALES_JWT_SECRET not set",
            file=sys.stderr,
        )
        return 2

    token = mint_edge_token(args.device_id, ttl=ttl, secret=secret)
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
