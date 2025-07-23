#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Kafka Connect workload class and methods."""

import logging
import os
import socket
import subprocess
from contextlib import closing
from typing import Iterable, Mapping

from charmlibs import pathops
from charms.operator_libs_linux.v2 import snap
from ops import pebble
from tenacity import retry, retry_if_result, stop_after_attempt, wait_fixed
from typing_extensions import override

from core.workload import WorkloadBase
from literals import GROUP, USER_NAME

logger = logging.getLogger(__name__)


class Workload(WorkloadBase):
    """Wrapper for performing common operations specific to the Kafka Connect Snap."""

    service: str
    SNAP_NAME = "charmed-kafka-ui"
    SERVICE_NAME = "daemon"

    def __init__(self) -> None:
        self.kafka_ui = snap.SnapCache()[self.SNAP_NAME]
        self.root = pathops.LocalPath("/")

    @override
    def start(self) -> None:
        try:
            self.kafka_ui.start(services=[self.SERVICE_NAME])
        except snap.SnapError as e:
            logger.exception(str(e))

    @override
    def stop(self) -> None:
        try:
            self.kafka_ui.stop(services=[self.SERVICE_NAME])
        except snap.SnapError as e:
            logger.exception(str(e))

    @override
    def restart(self) -> None:
        try:
            self.kafka_ui.restart(services=[self.SERVICE_NAME])
        except snap.SnapError as e:
            logger.exception(str(e))

    @override
    def read(self, path: str) -> list[str]:
        return (
            [] if not (self.root / path).exists() else (self.root / path).read_text().split("\n")
        )

    @override
    def write(self, content: str, path: str, mode: str = "w") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        (self.root / path).write_text(content, user=USER_NAME, group=GROUP)

    @override
    def exec(
        self,
        command: list[str] | str,
        env: Mapping[str, str] | None = None,
        working_dir: str | None = None,
    ) -> str:
        try:
            output = subprocess.check_output(
                command,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                shell=isinstance(command, str),
                env=env,
                cwd=working_dir,
            )
            logger.debug(f"{output=}")
            return output
        except subprocess.CalledProcessError as e:
            logger.error(f"cmd failed - cmd={e.cmd}, stdout={e.stdout}, stderr={e.stderr}")
            raise e

    @override
    @retry(
        wait=wait_fixed(1),
        stop=stop_after_attempt(5),
        retry=retry_if_result(lambda result: result is False),
        retry_error_callback=lambda _: False,
    )
    def active(self) -> bool:
        try:
            return bool(self.kafka_ui.services[self.SERVICE_NAME]["active"])
        except KeyError:
            return False

    def install(self) -> bool:
        """Installs the Charmed Kafka UI snap."""
        try:
            os.system(
                "wget https://github.com/imanenami/test-snaps/raw/refs/heads/main/charmed-kafka-ui_1.2.0_amd64.snap"
            )
            os.system("snap install --dangerous ./charmed-kafka-ui_1.2.0_amd64.snap")
            # self.kafka_ui.ensure(snap.SnapState.Present, revision=SNAP_REVISION, channel="edge")
            # self.kafka_ui.hold()
        except snap.SnapError as e:
            logger.error(str(e))
            return False

        return True

    @override
    def check_socket(self, host: str, port: int) -> bool:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            return sock.connect_ex((host, port)) == 0

    @property
    @override
    def installed(self) -> bool:
        try:
            return bool(self.kafka_ui.services[self.SERVICE_NAME])
        except (KeyError, snap.SnapNotFoundError):
            return False

    @property
    @override
    def container_can_connect(self) -> bool:
        return True  # Always True on VM

    @property
    @override
    def layer(self) -> pebble.Layer:
        raise NotImplementedError

    @override
    def set_environment(self, env_vars: Iterable[str]) -> None:
        raw_current_env = self.read(self.paths.env)
        current_env = self.map_env(raw_current_env)

        updated_env = current_env | self.map_env(env_vars)
        content = "\n".join([f"{key}={value}" for key, value in updated_env.items()])
        self.write(content=content + "\n", path=self.paths.env)

    @staticmethod
    def map_env(env: Iterable[str]) -> dict[str, str]:
        """Parse env variables into a dict."""
        map_env = {}
        for var in env:
            key = "".join(var.split("=", maxsplit=1)[0])
            value = "".join(var.split("=", maxsplit=1)[1:])
            if key:
                # only check for keys, as we can have an empty value for a variable
                map_env[key] = value
        return map_env
