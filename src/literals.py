#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of globals common to the charm."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ops import ActiveStatus, BlockedStatus, MaintenanceStatus, StatusBase, WaitingStatus

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

SNAP_NAME = "charmed-kafka-ui"
SNAP_REVISION = "3"
CONTAINER = "kafka-ui"
SERVICE_NAME = "daemon"
USER_NAME = "_daemon_"
GROUP = "root"
CONFIG_DIR = f"/var/snap/{SNAP_NAME}/current/etc/kafka-ui"
SUBSTRATE = "vm"


DEFAULT_SECURITY_MECHANISM = "SCRAM-SHA-512"
PEER_REL = "cluster"
KAFKA_REL = "kafka-client"
KAFKA_CONNECT_REL = "connect-client"
KARAPACE_REL = "karapace-client"
OAUTH_REL = "oauth"
OAUTH_CLIENT_NAME = "custom"
TLS_REL = "certificates"

Substrates = Literal["vm", "k8s"]
DebugLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


@dataclass()
class StatusLevel:
    """Status object helper."""

    status: StatusBase
    log_level: LogLevel


class Status(Enum):
    """Collection of possible statuses for the charm."""

    SNAP_NOT_INSTALLED = StatusLevel(BlockedStatus(f"unable to install {SNAP_NAME} snap"), "ERROR")
    INSTALLING = StatusLevel(MaintenanceStatus(f"installing {SNAP_NAME}"), "DEBUG")
    MISSING_KAFKA = StatusLevel(BlockedStatus("application needs Kafka client relation"), "DEBUG")
    NO_KAFKA_CREDENTIALS = StatusLevel(
        WaitingStatus("waiting for Kafka cluster credentials"), "DEBUG"
    )
    SERVICE_NOT_RUNNING = StatusLevel(BlockedStatus("service is not running"), "WARNING")
    SERVICE_STARTING = StatusLevel(WaitingStatus("service is still starting up"), "INFO")
    SERVICE_UNHEALTHY = StatusLevel(BlockedStatus("service is unable to handle requests"), "ERROR")

    ACTIVE = StatusLevel(ActiveStatus(), "DEBUG")
