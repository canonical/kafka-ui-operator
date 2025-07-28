#!/usr/bin/env python3
# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Charm the application."""

import logging

import ops
import requests
from charms.data_platform_libs.v0.data_interfaces import (
    KafkaConnectRequirerEventHandlers,
    KafkaRequirerEventHandlers,
)
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.data_platform_libs.v0.karapace import KarapaceRequiresEventHandlers
from ops import CollectStatusEvent
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed

from core.models import Context
from core.structured_config import CharmConfig
from events.tls import TLSHandler
from literals import KAFKA_CONNECT_REL, KAFKA_REL, KARAPACE_REL, SUBSTRATE, DebugLevel, Status
from managers.config import ConfigManager
from managers.tls import TLSManager
from workload import Workload

logger = logging.getLogger(__name__)


class KafkaUiCharm(TypedCharmBase[CharmConfig]):
    """Charm the application."""

    config_type = CharmConfig

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self.workload = Workload()
        self.context = Context(self)
        self.pending_inactive_statuses: list[Status] = []

        # Managers
        self.config_manager = ConfigManager(
            context=self.context, workload=self.workload, config=self.config
        )
        self.tls_manager = TLSManager(
            context=self.context, workload=self.workload, substrate=SUBSTRATE
        )

        # Handlers
        self.kafka_events = KafkaRequirerEventHandlers(self, self.context.kafka_client_interface)
        self.connect_events = KafkaConnectRequirerEventHandlers(
            self, self.context.connect_client_interface
        )
        self.karapace_events = KarapaceRequiresEventHandlers(
            self, self.context.karapace_client_interface
        )
        self.tls = TLSHandler(self)

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)
        self.framework.observe(self.on.collect_app_status, self._on_collect_status)

        for relation in [KAFKA_REL, KAFKA_CONNECT_REL, KARAPACE_REL]:
            self.framework.observe(self.on[relation].relation_changed, self._on_config_changed)
            self.framework.observe(self.on[relation].relation_broken, self._on_config_changed)

    def _on_install(self, _: ops.EventBase) -> None:
        """Handle `install` event."""
        if not self.workload.install():
            self._set_status(Status.SNAP_NOT_INSTALLED)
            return

    def _on_config_changed(self, event: ops.EventBase) -> None:
        """Handle `config-changed` and general client `relation-changed` events."""
        if not all([self.workload.container_can_connect, self.context.app]):
            event.defer()
            return

        self.tls.init_unit_tls()

        if not self.context.app.admin_password:
            self.context.app.update(
                {self.context.app.ADMIN_PASSWORD: self.workload.generate_password()}
            )

        config_changed = self.config_manager.config_changed()
        truststore_changed = self.tls_manager.truststore_changed()

        if not any([config_changed, truststore_changed]):
            return

        if truststore_changed:
            self.tls_manager.update_truststore()

        self.workload.set_environment(env_vars=self.config_manager.java_opts)
        self.workload.write(
            content=self.config_manager.clean_yaml_config,
            path=self.workload.paths.application_local_config,
        )

        self.workload.restart()

    def _on_update_status(self, _) -> None:
        """Handle `update-status` event."""
        logger.debug("Update status, emitting config-changed")
        self.on.config_changed.emit()

    def _on_upgrade_charm(self, _: ops.EventBase) -> None:
        """Handle `upgrade-charm` event."""
        if not self.workload.install():
            self._set_status(Status.SNAP_NOT_INSTALLED)
            return

        self.on.config_changed.emit()

    def _set_status(self, key: Status) -> None:
        """Set charm status."""
        status: ops.StatusBase = key.value.status
        log_level: DebugLevel = key.value.log_level

        getattr(logger, log_level.lower())(status.message)
        self.pending_inactive_statuses.append(key)

    def _on_collect_status(self, event: CollectStatusEvent):
        """Handle `collect-status` event."""
        self.health_check()
        for status in self.pending_inactive_statuses + [Status.ACTIVE]:
            event.add_status(status.value.status)

    @retry(
        wait=wait_fixed(1),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(lambda _: True),
        retry_error_callback=lambda _: False,
    )
    def health_check(self) -> bool:
        """Check if workload and web server are healthy and up."""
        if not all([self.workload.container_can_connect, self.workload.installed]):
            self._set_status(Status.INSTALLING)
            return False

        if not self.context.kafka_client.ready:
            self._set_status(Status.MISSING_KAFKA)
            return False

        if not self.workload.active():
            self._set_status(Status.SERVICE_NOT_RUNNING)
            return False

        resp = requests.get(self.context.endpoint, verify=False, timeout=2)

        if not resp.status_code == 200:
            self._set_status(Status.SERVICE_UNHEALTHY)
            return False

        return True


if __name__ == "__main__":  # pragma: nocover
    ops.main(KafkaUiCharm)
