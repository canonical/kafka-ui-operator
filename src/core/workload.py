#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Workload base interface definition."""

import secrets
import string
from abc import ABC, abstractmethod
from typing import BinaryIO, Iterable

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


class WorkloadBase(ABC):
    """Base interface for common workload operations."""

    paths: Paths = Paths()

    @abstractmethod
    def start(self) -> None:
        """Starts the workload service."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stops the workload service."""
        ...

    @abstractmethod
    def restart(self) -> None:
        """Restarts the workload service."""
        ...

    @abstractmethod
    def read(self, path: str) -> list[str]:
        """Reads a file from the workload.

        Args:
            path: the full filepath to read from

        Returns:
            List of string lines from the specified path
        """
        ...

    @abstractmethod
    def write(self, content: str | BinaryIO, path: str, mode: str = "w") -> None:
        """Writes content to a workload file.

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
        """Runs a command on the workload substrate."""
        ...

    @abstractmethod
    def active(self) -> bool:
        """Checks that the workload is active."""
        ...

    @abstractmethod
    def check_socket(self, host: str, port: int) -> bool:
        """Checks whether an IPv4 socket is healthy or not."""
        ...

    @abstractmethod
    def set_environment(self, env_vars: Iterable[str]) -> None:
        """Updates the environment variables with provided iterable of key=value `env_vars`."""
        ...

    @property
    @abstractmethod
    def installed(self) -> bool:
        """Whether the workload service is installed or not."""
        ...

    @property
    @abstractmethod
    def layer(self) -> Layer:
        """Gets the Pebble Layer definition for the current workload."""
        ...

    @property
    @abstractmethod
    def container_can_connect(self) -> bool:
        """Flag to check if workload container can connect."""
        ...

    @staticmethod
    def generate_password(length: int = 32) -> str:
        """Creates randomized string of arbitrary `length` (default is 32) for use as app passwords."""
        return "".join(
            [secrets.choice(string.ascii_letters + string.digits) for _ in range(length)]
        )
