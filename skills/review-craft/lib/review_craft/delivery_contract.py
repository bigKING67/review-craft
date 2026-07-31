from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import DELIVERY_SCHEMA_VERSION
from .contracts import ContractError
from .jsonio import read_json, sha256_bytes, sha256_json
from .schema_validation import validate_instance

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_delivery_schema(document: Any) -> None:
    schema = read_json(SCHEMA_ROOT / "delivery-attestation.schema.json")
    errors = [
        f"delivery-attestation.schema.json: {message}"
        for message in validate_instance(document, schema)
    ]
    if errors:
        raise ContractError(errors)


def delivery_file(directory: Path, relative: str) -> Path:
    path = directory / relative
    if path.is_symlink():
        raise ContractError([f"delivery artifact must not be a symlink: {relative}"])
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(directory)
    except (OSError, ValueError) as error:
        raise ContractError([f"invalid delivery artifact {relative}: {error}"]) from error
    if not resolved.is_file():
        raise ContractError([f"delivery artifact must be a file: {relative}"])
    return resolved


def artifact_reference(path: Path, relative: str) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": relative,
        "sha256": sha256_bytes(content),
        "sizeBytes": len(content),
    }


def attestation_base_id(document: dict[str, Any]) -> str:
    value = document["attestedAt"]
    instant = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    stamp = instant.strftime("%Y%m%dT%H%M%SZ")
    payload = {key: value for key, value in document.items() if key != "deliveryId"}
    return f"rcd-{stamp}-{sha256_json(payload)[:12]}"


def delivery_id_matches(document: dict[str, Any]) -> bool:
    base = attestation_base_id(document)
    return bool(
        re.fullmatch(
            rf"{re.escape(base)}(?:-(?:[2-9]|[1-9][0-9]+))?",
            document["deliveryId"],
        )
    )


def delivery_status(
    *,
    source_status: str,
    push_requested: bool,
    push_status: str,
    ci_requested: bool,
    ci_status: str,
) -> str:
    if source_status == "FAILED":
        return "FAILED"
    if push_requested and push_status == "FAILED":
        return "FAILED"
    if ci_requested and ci_status == "FAILED":
        return "FAILED"
    if push_status != "VERIFIED":
        return "PARTIAL"
    if ci_requested and ci_status != "VERIFIED":
        return "PARTIAL"
    return "VERIFIED"


def validate_artifact_reference(
    directory: Path,
    reference: dict[str, Any],
    *,
    expected_path: str | None = None,
) -> Path:
    relative = reference.get("path")
    if not isinstance(relative, str):
        raise ContractError(["delivery artifact reference path must be a string"])
    errors: list[str] = []
    if expected_path is not None and relative != expected_path:
        errors.append(f"delivery artifact path must be {expected_path}")
    try:
        path = delivery_file(directory, relative)
    except ContractError as error:
        errors.extend(error.errors)
        path = directory / relative
    else:
        content = path.read_bytes()
        if reference.get("sha256") != sha256_bytes(content):
            errors.append(f"delivery artifact {relative}: sha256 mismatch")
        if reference.get("sizeBytes") != len(content):
            errors.append(f"delivery artifact {relative}: sizeBytes mismatch")
    if errors:
        raise ContractError(errors)
    return path


def validate_delivery_state(directory: Path, attestation: dict[str, Any]) -> None:
    state_path = delivery_file(directory, "delivery-state.json")
    state = read_json(state_path)
    errors: list[str] = []
    if not isinstance(state, dict):
        errors.append("delivery-state.json: expected an object")
    else:
        if state.get("documentType") != "review-craft.delivery-state":
            errors.append("delivery-state.documentType: invalid value")
        if state.get("schemaVersion") != DELIVERY_SCHEMA_VERSION:
            errors.append("delivery-state.schemaVersion: invalid value")
        if state.get("deliveryId") != attestation["deliveryId"]:
            errors.append("delivery-state.deliveryId: does not match attestation")
        if state.get("attestationSha256") != sha256_json(attestation):
            errors.append("delivery-state.attestationSha256: does not match attestation")
    if errors:
        raise ContractError(errors)
