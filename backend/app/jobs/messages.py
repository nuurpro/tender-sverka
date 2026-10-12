"""Strict versioned HMAC envelopes; broker arguments contain identities only."""
import hashlib
import hmac
import re
from uuid import UUID

from app.workspace.repository import canonical_json

FIELDS = {"version", "organization_id", "job_id", "input_hash", "signature"}


def signing_key(secret):
    if not isinstance(secret, str) or len(secret) < 32:
        raise ValueError("A separate job signing secret of at least 32 characters is required")
    return secret.encode()


def envelope(organization_id, job_id, input_hash, secret):
    data = {"version": 1, "organization_id": str(UUID(organization_id)),
        "job_id": str(UUID(job_id)), "input_hash": input_hash}
    if not isinstance(input_hash, str) or not re.fullmatch("[a-f0-9]{64}", input_hash):
        raise ValueError("Invalid job identity")
    data["signature"] = hmac.new(signing_key(secret), canonical_json(data).encode(), hashlib.sha256).hexdigest()
    return data


def validate_envelope(message, secret):
    signing_key(secret)
    try:
        if not isinstance(message, dict) or set(message) != FIELDS or type(message["version"]) is not int or message["version"] != 1:
            return None
        expected = envelope(message["organization_id"], message["job_id"], message["input_hash"], secret)
        if not isinstance(message["signature"], str) or not hmac.compare_digest(expected["signature"], message["signature"]):
            return None
        return expected
    except (ValueError, TypeError, AttributeError):
        return None
