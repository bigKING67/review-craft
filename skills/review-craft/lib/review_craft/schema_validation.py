from __future__ import annotations

import re
from datetime import datetime
from typing import Any

SUPPORTED_KEYWORDS = {
    "$schema",
    "$id",
    "$defs",
    "$ref",
    "title",
    "description",
    "default",
    "type",
    "const",
    "enum",
    "required",
    "properties",
    "additionalProperties",
    "minProperties",
    "minLength",
    "pattern",
    "format",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "items",
}


def _resolve_pointer(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"unsupported schema reference: {reference}")
    value: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"unresolved schema reference: {reference}")
        value = value[part]
    if not isinstance(value, dict):
        raise ValueError(f"schema reference is not an object: {reference}")
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValueError(f"unsupported schema type: {expected}")


def _valid_datetime(value: str) -> bool:
    if re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})",
        value,
    ) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_instance(
    instance: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    root_schema = root_schema or schema
    errors: list[str] = []
    unknown = sorted(set(schema) - SUPPORTED_KEYWORDS)
    if unknown:
        errors.append(f"{path}: unsupported schema keywords {', '.join(unknown)}")
        return errors

    reference = schema.get("$ref")
    if reference is not None:
        try:
            target = _resolve_pointer(root_schema, reference)
        except ValueError as error:
            return [f"{path}: {error}"]
        return validate_instance(instance, target, root_schema=root_schema, path=path)

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(expected_types, list) or not all(
            isinstance(item, str) for item in expected_types
        ):
            return [f"{path}: invalid schema type declaration"]
        try:
            type_matches = any(_matches_type(instance, item) for item in expected_types)
        except ValueError as error:
            return [f"{path}: {error}"]
        if not type_matches:
            errors.append(f"{path}: expected type {' or '.join(expected_types)}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}.{key}: required property is missing")
        minimum_properties = schema.get("minProperties")
        if minimum_properties is not None and len(instance) < minimum_properties:
            errors.append(f"{path}: expected at least {minimum_properties} properties")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key in sorted(instance):
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(
                    validate_instance(
                        instance[key], properties[key], root_schema=root_schema, path=child_path
                    )
                )
            elif additional is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(additional, dict):
                errors.extend(
                    validate_instance(
                        instance[key], additional, root_schema=root_schema, path=child_path
                    )
                )

    if isinstance(instance, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if minimum_items is not None and len(instance) < minimum_items:
            errors.append(f"{path}: expected at least {minimum_items} items")
        if maximum_items is not None and len(instance) > maximum_items:
            errors.append(f"{path}: expected at most {maximum_items} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                errors.extend(
                    validate_instance(
                        value, item_schema, root_schema=root_schema, path=f"{path}[{index}]"
                    )
                )

    if isinstance(instance, str):
        minimum_length = schema.get("minLength")
        if minimum_length is not None and len(instance) < minimum_length:
            errors.append(f"{path}: expected at least {minimum_length} characters")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, instance) is None:
            errors.append(f"{path}: does not match pattern {pattern!r}")
        value_format = schema.get("format")
        if value_format == "date-time" and not _valid_datetime(instance):
            errors.append(f"{path}: expected an RFC 3339 date-time")
        elif value_format not in {None, "date-time"}:
            errors.append(f"{path}: unsupported schema format {value_format!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and instance < minimum:
            errors.append(f"{path}: expected a value >= {minimum}")
        if maximum is not None and instance > maximum:
            errors.append(f"{path}: expected a value <= {maximum}")
    return errors
