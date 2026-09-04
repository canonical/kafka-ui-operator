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
PORT = 8080

DEFAULT_SECURITY_MECHANISM = "SCRAM-SHA-512"
PEER_REL = "cluster"
KAFKA_REL = "kafka-client"
KAFKA_CONNECT_REL = "connect-client"
KARAPACE_REL = "karapace-client"
TLS_REL = "certificates"
OAUTH_REL = "oauth"
OAUTH_CA_REL = "oauth-ca"
INGRESS_REL = "ingress"

OAUTH_CA_ALIAS_PREFIX = "oauth-ca-"
JAVA_CACERTS_DEFAULT_PASSWORD = "changeit"

CLUSTER_NAME = "kafka"
RBAC_SUBJECT_PROVIDER = "oauth"

# Predefined RBAC roles
ADMIN_ROLE = "admin"
CHARMED_MANAGER_ROLE = "charmed_manager"
CHARMED_USER_ROLE = "charmed_user"
CHARMED_READ_ROLE = "charmed_read"
CHARMED_STATS_ROLE = "charmed_stats"

ADMIN_PERMISSIONS = [
    {"resource": "applicationconfig", "actions": "view"},
    {"resource": "clusterconfig", "actions": "view"},
    {"resource": "topic", "value": ".*", "actions": "all"},
    {"resource": "consumer", "value": ".*", "actions": "all"},
    {"resource": "schema", "value": ".*", "actions": "all"},
    {"resource": "connect", "value": ".*", "actions": "all"},
    {"resource": "acl", "actions": ["view", "edit"]},
]

# Regex matching user-facing topics while excluding internal/system topics
# (leading '_', Kafka Connect/MirrorMaker2 internal topics, and MM2 checkpoint/heartbeat topics).
USER_TOPIC_REGEX = (
    r"^(?!_)(?!connect-(?:offsets|configs|status)$)(?!mm2-)(?!heartbeats$)(?!checkpoints$).*"
)
# Regex matching non-internal consumer groups.
USER_CONSUMER_REGEX = r"^(?!_)(?!connect-)(?!mm2-).*"

CHARMED_MANAGER_PERMISSIONS = [
    {
        "resource": "topic",
        "value": USER_TOPIC_REGEX,
        "actions": [
            "view",
            "create",
            "edit",
            "delete",
            "messages_read",
            "messages_produce",
            "messages_delete",
            "analysis_run",
            "analysis_view",
        ],
    },
    {"resource": "topic", "value": ".*", "actions": ["view"]},
    {"resource": "consumer", "value": ".*", "actions": ["view"]},
    {"resource": "consumer", "value": USER_CONSUMER_REGEX, "actions": ["reset_offsets", "delete"]},
    {"resource": "schema", "value": ".*", "actions": ["view"]},
]

CHARMED_USER_PERMISSIONS = [
    {
        "resource": "topic",
        "value": USER_TOPIC_REGEX,
        "actions": ["view", "messages_read", "messages_produce"],
    },
    {"resource": "topic", "value": ".*", "actions": ["view"]},
    {"resource": "consumer", "value": ".*", "actions": ["view"]},
    {"resource": "schema", "value": ".*", "actions": ["view"]},
]

CHARMED_READ_PERMISSIONS = [
    {
        "resource": "topic",
        "value": USER_TOPIC_REGEX,
        "actions": ["view", "messages_read"],
    },
    {"resource": "topic", "value": ".*", "actions": ["view"]},
    {"resource": "consumer", "value": ".*", "actions": ["view"]},
    {"resource": "schema", "value": ".*", "actions": ["view"]},
]

CHARMED_STATS_PERMISSIONS = [
    {"resource": "topic", "value": ".*", "actions": ["view"]},
    {"resource": "consumer", "value": ".*", "actions": ["view"]},
    {"resource": "schema", "value": ".*", "actions": ["view"]},
    {"resource": "connect", "value": ".*", "actions": ["view"]},
    {"resource": "clusterconfig", "actions": ["view"]},
]

ROLE_PERMISSIONS = {
    ADMIN_ROLE: ADMIN_PERMISSIONS,
    CHARMED_MANAGER_ROLE: CHARMED_MANAGER_PERMISSIONS,
    CHARMED_USER_ROLE: CHARMED_USER_PERMISSIONS,
    CHARMED_READ_ROLE: CHARMED_READ_PERMISSIONS,
    CHARMED_STATS_ROLE: CHARMED_STATS_PERMISSIONS,
}

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
    MISSING_INGRESS_HA = StatusLevel(
        BlockedStatus("application needs an ingress relation when multiple units are deployed."),
        "WARNING",
    )
    SERVICE_NOT_RUNNING = StatusLevel(BlockedStatus("service is not running"), "WARNING")
    SERVICE_STARTING = StatusLevel(WaitingStatus("service is still starting up"), "INFO")
    SERVICE_UNHEALTHY = StatusLevel(BlockedStatus("service is unable to handle requests"), "ERROR")

    ACTIVE = StatusLevel(ActiveStatus(), "DEBUG")
