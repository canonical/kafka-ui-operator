from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ops import ActiveStatus, BlockedStatus, MaintenanceStatus, StatusBase, WaitingStatus

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

SNAP_NAME = "charmed-kafka-ui"
SNAP_REVISION = "1"
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
    INSTALLING = StatusLevel(MaintenanceStatus(f"Installing {SNAP_NAME}"), "DEBUG")
    MISSING_KAFKA = StatusLevel(BlockedStatus("Application needs Kafka client relation"), "DEBUG")
    NO_KAFKA_CREDENTIALS = StatusLevel(
        WaitingStatus("Waiting for Kafka cluster credentials"), "DEBUG"
    )
    SERVICE_NOT_RUNNING = StatusLevel(BlockedStatus("Worker service is not running"), "WARNING")
    SERVICE_STARTING = StatusLevel(WaitingStatus("Worker is still starting up"), "INFO")
    SERVICE_UNHEALTHY = StatusLevel(BlockedStatus("Worker is unable to handle requests"), "ERROR")

    ACTIVE = StatusLevel(ActiveStatus(), "DEBUG")
