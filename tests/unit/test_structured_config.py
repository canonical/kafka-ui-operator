#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError
from typing_extensions import override

from core.structured_config import CharmConfig
from literals import ADMIN_ROLE, CHARMED_MANAGER_ROLE, CHARMED_STATS_ROLE, ROLE_PERMISSIONS

from .helpers import CONFIG_DEFAULTS

logger = logging.getLogger(__name__)


@dataclass
class ConfigOverride:
    """Helper for overriding a single charm config option in parametrized tests."""

    key: str
    values: list[Any]
    valid: bool = True

    @override
    def __str__(self) -> str:
        state = "VALID" if self.valid else "INVALID"
        return f"{self.key}: {','.join(str(value) for value in self.values)} -> {state}"


def build_config(**overrides: Any) -> CharmConfig:
    """Build a `CharmConfig` from the charm's own defaults plus the given overrides."""
    return CharmConfig(**(CONFIG_DEFAULTS | overrides))


@pytest.mark.parametrize(
    "override",
    [
        ConfigOverride(
            key="username_attribute", values=["sub", "email", "preferred_username", "name"]
        ),
        ConfigOverride(
            key="username_attribute",
            values=["username", "e-mail", "", None, 123, True],
            valid=False,
        ),
        ConfigOverride(
            key="roles_mapping",
            values=[
                "{}",
                json.dumps({"someone@test.com": ADMIN_ROLE}),
                json.dumps({"a@test.com": CHARMED_MANAGER_ROLE, "b@test.com": CHARMED_STATS_ROLE}),
            ],
        ),
        ConfigOverride(
            key="roles_mapping",
            values=[
                "not-json",
                "{'single': 'quotes'}",
                json.dumps(["a-list", "not-an-object"]),
                json.dumps({"someone@test.com": "superuser"}),
                json.dumps({"someone@test.com": ADMIN_ROLE, "other@test.com": "nope"}),
            ],
            valid=False,
        ),
    ],
    ids=lambda override: f"{override}",
)
def test_validator(override: ConfigOverride) -> None:
    """Checks `CharmConfig` accepts every valid value and rejects every invalid one."""
    for value in override.values:
        if override.valid:
            config = build_config(**{override.key: value})
            assert getattr(config, override.key) is not None
        else:
            with pytest.raises(ValidationError):
                build_config(**{override.key: value})


def test_defaults_are_valid() -> None:
    """Checks the defaults declared in `config.yaml` build a valid `CharmConfig`."""
    # Given / When
    config = build_config()

    # Then
    assert config.roles_mapping == {}
    assert config.username_attribute == "email"
    assert config.system_users is None


def test_roles_mapping_is_parsed_into_a_dict() -> None:
    """Checks the raw JSON string reaches the charm as a mapping of user to role."""
    # Given
    mapping = {"admin@test.com": ADMIN_ROLE, "stats@test.com": CHARMED_STATS_ROLE}

    # When
    config = build_config(roles_mapping=json.dumps(mapping))

    # Then
    assert config.roles_mapping == mapping


def test_roles_mapping_accepts_every_predefined_role() -> None:
    """Checks every role the charm ships permissions for is accepted by the validator."""
    # Given
    mapping = {f"user-{index}@test.com": role for index, role in enumerate(ROLE_PERMISSIONS)}

    # When
    config = build_config(roles_mapping=json.dumps(mapping))

    # Then
    assert config.roles_mapping == mapping


def test_roles_mapping_error_names_the_offending_roles() -> None:
    """Checks an unknown role is reported by name, alongside the allowed roles."""
    # Given
    mapping = {"a@test.com": "wizard", "b@test.com": "sorcerer", "c@test.com": ADMIN_ROLE}

    # When
    with pytest.raises(ValidationError) as excinfo:
        build_config(roles_mapping=json.dumps(mapping))

    # Then
    message = str(excinfo.value)
    assert "sorcerer" in message
    assert "wizard" in message
    assert ADMIN_ROLE in message
