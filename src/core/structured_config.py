#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the Kafka UI charm."""

import json
import logging
from typing import Literal

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import validator

from literals import ROLE_PERMISSIONS

logger = logging.getLogger(__name__)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    system_users: str | None = None
    roles_mapping: dict[str, str] | None = None
    username_attribute: Literal["sub", "email", "preferred_username", "name"]

    @validator("roles_mapping", pre=True)
    @classmethod
    def roles_mapping_validator(cls, value: str) -> dict[str, str]:
        """Validate the roles-mapping configuration option."""
        try:
            mapping = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Invalid roles-mapping JSON; ignoring")
            raise ValueError("Invalid roles-mapping JSON.")

        if not isinstance(mapping, dict):
            raise ValueError("roles-mapping must be a JSON object mapping usernames to roles.")

        invalid_roles = sorted({role for role in mapping.values() if role not in ROLE_PERMISSIONS})
        if invalid_roles:
            logger.warning("Invalid roles in roles-mapping: %s", ", ".join(invalid_roles))
            raise ValueError(
                f"Invalid role(s) in roles-mapping: {', '.join(invalid_roles)}. "
                f"Allowed roles are: {', '.join(ROLE_PERMISSIONS)}."
            )

        return mapping
