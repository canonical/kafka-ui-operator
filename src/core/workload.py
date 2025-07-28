#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Workload base interface definition."""

import secrets
import string
from abc import ABC, abstractmethod
from typing import BinaryIO, Iterable

from charmlibs import pathops
from ops.pebble import Layer

from literals import CONFIG_DIR


class Paths:
    """Object to store common paths for Kafka Connect worker."""

    def __init__(self, config_dir: str = CONFIG_DIR):
        self.config_dir = CONFIG_DIR

    @property
    def env(self) -> str:
        """Path to environment file."""
        return "/etc/environment"

    @property
    def application_local_config(self) -> str:
        """Path to the UI configuration file."""
        return f"{self.config_dir}/application-local.yml"

    @property
    def keystore(self) -> str:
        """Path to Java Keystore containing service private-key and signed certificates."""
        return f"{self.config_dir}/keystore.p12"

    @property
    def truststore(self):
        """Path to Java Truststore containing trusted CAs + certificates."""
        return f"{self.config_dir}/truststore.jks"


class WorkloadBase(ABC):
    """Base interface for common workload operations."""

    paths: Paths = Paths()
    root: pathops.PathProtocol

    @abstractmethod
    def start(self) -> None:
        """Start the workload service."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the workload service."""
        ...

    @abstractmethod
    def restart(self) -> None:
        """Restart the workload service."""
        ...

    @abstractmethod
    def read(self, path: str) -> list[str]:
        """Read a file from the workload.

        Args:
            path: the full filepath to read from

        Returns:
            List of string lines from the specified path
        """
        ...

    @abstractmethod
    def write(self, content: str | BinaryIO, path: str, mode: str = "w") -> None:
        """Write content to a workload file.

        Args:
            content: string of content to write
            path: the full filepath to write to
            mode: the write mode. Usually "w" for write, or "a" for append. Default "w"
        """
        ...

    @abstractmethod
    def exec(
        self,
        command: list[str] | str,
        env: dict[str, str] | None = None,
        working_dir: str | None = None,
        sensitive: bool = False,
    ) -> str:
        """Run a command on the workload substrate."""
        ...

    @abstractmethod
    def active(self) -> bool:
        """Check that the workload is active."""
        ...

    @abstractmethod
    def check_socket(self, host: str, port: int) -> bool:
        """Check whether an IPv4 socket is healthy or not."""
        ...

    @abstractmethod
    def set_environment(self, env_vars: Iterable[str]) -> None:
        """Update the environment variables with provided iterable of key=value `env_vars`."""
        ...

    @property
    @abstractmethod
    def installed(self) -> bool:
        """Check whether the workload service is installed or not."""
        ...

    @property
    @abstractmethod
    def layer(self) -> Layer:
        """Get the Pebble Layer definition for the current workload."""
        ...

    @property
    @abstractmethod
    def container_can_connect(self) -> bool:
        """Flag to check if workload container can connect."""
        ...

    @staticmethod
    def generate_password(length: int = 32) -> str:
        """Create randomized string of arbitrary `length` (default is 32)."""
        return "".join(
            [secrets.choice(string.ascii_letters + string.digits) for _ in range(length)]
        )
