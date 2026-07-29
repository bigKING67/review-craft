from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from .constants import PROFILES, REVIEW_MODES, SCORE_DIMENSIONS
from .jsonio import read_json


def default_config() -> dict[str, Any]:
    return {
        "mode": "review",
        "profile": "auto",
        "focusDimensions": [],
        "diffBase": None,
        "scope": ["."],
        "exclude": [],
        "generated": [],
        "vendored": [],
        "commands": {},
        "policy": {
            "allowNetwork": False,
            "allowInstall": False,
            "allowRepositoryMutation": False,
            "outputOutsideRepository": True,
        },
        "reportLanguage": "zh-CN",
    }


def validate_config(config: dict[str, Any]) -> None:
    for field in ("scope", "exclude", "generated", "vendored"):
        if not isinstance(config[field], list) or not all(
            isinstance(item, str) and item for item in config[field]
        ):
            raise ValueError(f"config.{field}: expected an array of strings")
    if not config["scope"]:
        raise ValueError("config.scope: must not be empty")
    if config["mode"] not in REVIEW_MODES:
        raise ValueError(f"config.mode: expected one of {', '.join(sorted(REVIEW_MODES))}")
    if config["profile"] not in PROFILES:
        raise ValueError(f"config.profile: expected one of {', '.join(sorted(PROFILES))}")
    focus_dimensions = config["focusDimensions"]
    valid_dimensions = {row[0] for row in SCORE_DIMENSIONS}
    if not isinstance(focus_dimensions, list) or not all(
        isinstance(item, str) and item in valid_dimensions for item in focus_dimensions
    ):
        raise ValueError("config.focusDimensions: expected canonical score dimension IDs")
    if len(focus_dimensions) != len(set(focus_dimensions)):
        raise ValueError("config.focusDimensions: duplicate dimension")
    if config["diffBase"] is not None and not (
        isinstance(config["diffBase"], str) and config["diffBase"].strip()
    ):
        raise ValueError("config.diffBase: expected a non-empty string or null")
    if config["reportLanguage"] != "zh-CN":
        raise ValueError("config.reportLanguage: only zh-CN is supported")
    commands = config["commands"]
    if not isinstance(commands, dict):
        raise ValueError("config.commands: expected an object")
    for name, command in commands.items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or not isinstance(command, dict):
            raise ValueError(f"config.commands.{name}: invalid command")
        argv = command.get("argv")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(item, str) and item and "\0" not in item for item in argv
        ):
            raise ValueError(f"config.commands.{name}.argv: expected a non-empty string array")
        cwd = command.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError(f"config.commands.{name}.cwd: expected a string")
        timeout = command.get("timeoutSeconds", 600)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
            raise ValueError(
                f"config.commands.{name}.timeoutSeconds: expected a positive integer"
            )


def load_config(path: Path | None) -> dict[str, Any]:
    config = default_config()
    if path is not None:
        supplied = read_json(path.expanduser().resolve(strict=True))
        if not isinstance(supplied, dict):
            raise ValueError("config: expected a JSON object")
        supplied = {key: value for key, value in supplied.items() if key != "$schema"}
        unknown = set(supplied) - set(config)
        if unknown:
            raise ValueError(f"config: unsupported fields {', '.join(sorted(unknown))}")
        for key, value in supplied.items():
            if key == "policy":
                if not isinstance(value, dict):
                    raise ValueError("config.policy: expected an object")
                unknown_policy = set(value) - set(config["policy"])
                if unknown_policy:
                    raise ValueError(
                        "config.policy: unsupported fields "
                        f"{', '.join(sorted(unknown_policy))}"
                    )
                if not all(isinstance(item, bool) for item in value.values()):
                    raise ValueError("config.policy: expected boolean values")
                config["policy"].update(value)
            else:
                config[key] = value
    validate_config(config)
    return config


def focus_values(values: list[str] | None) -> list[str]:
    if not values:
        return []
    requested = {
        item.strip()
        for value in values
        for item in value.split(",")
        if item.strip()
    }
    valid = [row[0] for row in SCORE_DIMENSIONS]
    unknown = sorted(requested - set(valid))
    if unknown:
        raise ValueError(f"--focus: unsupported dimensions {', '.join(unknown)}")
    return [identifier for identifier in valid if identifier in requested]


def effective_preflight_config(
    config: dict[str, Any],
    *,
    mode: str | None,
    base: str | None,
    focus: list[str] | None,
) -> tuple[dict[str, Any], str | None]:
    effective = copy.deepcopy(config)
    if mode:
        effective["mode"] = mode
    if base:
        if mode and mode != "diff":
            raise ValueError("--base can only be used with --mode diff")
        effective["mode"] = "diff"
        effective["diffBase"] = base
    selected_focus = focus_values(focus)
    if selected_focus:
        effective["focusDimensions"] = selected_focus
        if effective["mode"] == "review":
            effective["mode"] = "focus"
    validate_config(effective)
    effective_mode = effective["mode"]
    if effective_mode == "diff" and not effective["diffBase"]:
        raise ValueError("diff mode requires --base or config.diffBase")
    if effective_mode != "diff" and effective["diffBase"] is not None:
        raise ValueError("config.diffBase is only valid in diff mode")
    if effective_mode == "focus" and not effective["focusDimensions"]:
        raise ValueError("focus mode requires --focus or config.focusDimensions")
    if effective_mode == "review" and effective["focusDimensions"]:
        raise ValueError("review mode cannot declare focus dimensions")
    return effective, effective["diffBase"]
