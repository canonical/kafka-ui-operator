#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the Kafka UI charm."""

import logging

from charms.data_platform_libs.v0.data_models import BaseConfigModel

logger = logging.getLogger(__name__)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""
