#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the Kafka UI charm."""

import json
import logging

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import field_validator

logger = logging.getLogger(__name__)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    roles_mapping: dict = {}
    system_users: str | None = None
    username_attribute: str = "sub"

    @field_validator("roles_mapping", mode="before")
    @classmethod
    def parse_roles_mapping_to_dict(cls, v: str) -> dict:
        """Validate a string representation of a dict."""
        try:
            return json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError("roles-mapping is not a valid JSON dictionary") from e
