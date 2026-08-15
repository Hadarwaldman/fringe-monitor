"""Residential-proxy egress support.

The edfringe API is behind Cloudflare, which 403s AWS datacenter IPs. Lambdas
route all edfringe traffic through a residential proxy whose URL lives in the
SSM SecureString named by FRINGE_PROXY_PARAM. Loaded into FRINGE_PROXY_URL at
runtime; client.make_async_client() picks it up.
"""
from __future__ import annotations

import os


def load_proxy_into_env() -> str | None:
    """Populate FRINGE_PROXY_URL from the SSM SecureString named by
    FRINGE_PROXY_PARAM. Idempotent; returns the URL or None (direct egress).

    Only the parameter *name* is configured in Terraform; the secret value is
    written manually via `aws ssm put-parameter`.
    """
    if os.environ.get("FRINGE_PROXY_URL"):
        return os.environ["FRINGE_PROXY_URL"]
    param_name = os.environ.get("FRINGE_PROXY_PARAM")
    if not param_name:
        return None
    import boto3
    from botocore.exceptions import ClientError

    try:
        resp = boto3.client("ssm").get_parameter(Name=param_name, WithDecryption=True)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            print("warn: proxy SSM parameter not found; using direct egress", flush=True)
            return None
        raise
    url = (resp["Parameter"]["Value"] or "").strip()
    if not url:
        return None
    os.environ["FRINGE_PROXY_URL"] = url
    return url
