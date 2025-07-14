#!/usr/bin/env python3
# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Charm the application."""

import logging
import time

import ops
from charms.data_platform_libs.v0.data_interfaces import (
    KafkaConnectRequirerEventHandlers,
    KafkaRequirerEventHandlers,
)
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.data_platform_libs.v0.karapace import KarapaceRequiresEventHandlers

from core.models import Context
from core.structured_config import CharmConfig
from literals import KAFKA_CONNECT_REL, KAFKA_REL, KARAPACE_REL, DebugLevel, Status
from managers.config import ConfigManager
from workload import Workload

logger = logging.getLogger(__name__)


class KafkaUiCharm(TypedCharmBase[CharmConfig]):
    """Charm the application."""

    config_type = CharmConfig

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self.workload = Workload()
        self.context = Context(self)
        self.config_manager = ConfigManager(
            context=self.context, workload=self.workload, config=self.config
        )

        self.kafka_events = KafkaRequirerEventHandlers(self, self.context.kafka_client_interface)
        self.connect_events = KafkaConnectRequirerEventHandlers(
            self, self.context.connect_client_interface
        )
        self.karapace_events = KarapaceRequiresEventHandlers(
            self, self.context.karapace_client_interface
        )

        self.framework.observe(getattr(self.on, "install"), self._on_install)
        self.framework.observe(getattr(self.on, "upgrade_charm"), self._on_upgrade_charm)

        for relation in [KAFKA_REL, KAFKA_CONNECT_REL, KARAPACE_REL]:
            self.framework.observe(self.on[relation].relation_changed, self._on_config_changed)
            self.framework.observe(self.on[relation].relation_broken, self._on_config_changed)

    def _on_install(self, _: ops.EventBase) -> None:
        # if not self.workload.install():
        #     self._set_status(Status.SNAP_NOT_INSTALLED)
        #     return

        logger.info("INSTALL NOW PLEASE")
        time.sleep(30)
        logger.info("CONTINUING")

    def _on_config_changed(self, event: ops.EventBase) -> None:
        if not self.context.app:
            event.defer()
            return

        if not self.context.app.admin_password:
            self.context.app.update(
                {self.context.app.ADMIN_PASSWORD: self.workload.generate_password()}
            )

        self.workload.set_environment(env_vars=self.config_manager.java_opts)
        self.workload.write(
            content=self.config_manager.clean_yaml_config,
            path=self.workload.paths.application_local_config,
        )

        self.workload.restart()

    def _on_upgrade_charm(self, _: ops.EventBase) -> None:
        if not self.workload.install():
            self._set_status(Status.SNAP_NOT_INSTALLED)
            return

        self.on.config_changed.emit()

    def _set_status(self, key: Status) -> None:
        """Sets charm status."""
        status: ops.StatusBase = key.value.status
        log_level: DebugLevel = key.value.log_level

        getattr(logger, log_level.lower())(status.message)


if __name__ == "__main__":  # pragma: nocover
    ops.main(KafkaUiCharm)
