#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling Kafka UI OAuth configuration."""

import logging
from typing import TYPE_CHECKING

from charmlibs.interfaces.certificate_transfer import CertificateTransferRequires
from charmlibs.interfaces.oauth import ClientConfig, OAuthRequirer
from ops.framework import EventBase, Object

from literals import JAVA_CACERTS_DEFAULT_PASSWORD, OAUTH_CA_REL, OAUTH_REL

if TYPE_CHECKING:
    from charm import KafkaUiCharm

logger = logging.getLogger(__name__)


class OAuthHandler(Object):
    """Handler for managing Kafka UI oauth relations."""

    def __init__(self, charm: "KafkaUiCharm") -> None:
        super().__init__(charm, "oauth")
        self.charm: "KafkaUiCharm" = charm

        client_config = ClientConfig(
            audience=["kafka"],
            redirect_uri=f"{self.charm.context.ingress_url}/login/oauth2/code/iam",
            scope="openid profile email phone offline address",
            grant_types=["authorization_code"],
        )
        self.oauth = OAuthRequirer(self.charm, client_config, relation_name=OAUTH_REL)
        self.cert_transfer = CertificateTransferRequires(self.charm, OAUTH_CA_REL)

        self.framework.observe(
            self.charm.on[OAUTH_REL].relation_changed, self._on_oauth_relation_changed
        )
        self.framework.observe(
            self.charm.on[OAUTH_REL].relation_broken, self._on_oauth_relation_broken
        )
        self.framework.observe(
            self.cert_transfer.on.certificate_set_updated, self._on_oauth_ca_changed
        )
        self.framework.observe(
            self.cert_transfer.on.certificates_removed, self._on_oauth_ca_removed
        )

    def _on_oauth_relation_changed(self, event: EventBase) -> None:
        """Handle `_on_oauth_relation_changed` event."""
        if not self.charm.unit.is_leader():
            return

        provider_info = self.oauth.get_provider_info()
        if not (provider_info and provider_info.client_secret):
            event.defer()
            return

        self.charm.context.app.oauth_client_secret = provider_info.client_secret
        self.charm.on.config_changed.emit()

    def _on_oauth_relation_broken(self, event: EventBase) -> None:
        """Handle `_on_oauth_relation_broken` event."""
        if not self.charm.unit.is_leader():
            return

        self.charm.context.app.oauth_client_secret = ""
        self.charm.on.config_changed.emit()

    def _on_oauth_ca_changed(self, event: EventBase) -> None:
        """Reconcile the OAuth CA truststore when the transferred cert set changes."""
        if not self.charm.workload.container_can_connect:
            event.defer()
            return

        if not self.charm.workload.java_truststore_password:
            logger.debug("Truststore password not created yet, deferring truststore reconcile")
            event.defer()
            return

        if not self.cert_transfer.get_all_certificates():
            logger.debug("OAuth CA not transferred yet, deferring truststore reconcile")
            event.defer()
            return

        if self.reconcile_ca_truststore():
            self.charm.workload.restart()

    def _on_oauth_ca_removed(self, event: EventBase) -> None:
        """Drop the transferred OAuth CAs from the truststore once the relation is gone."""
        if not self.charm.workload.container_can_connect:
            event.defer()
            return

        if not self.charm.workload.java_truststore_password:
            logger.debug("Truststore password not created yet, deferring truststore reconcile")
            event.defer()
            return

        if self.reconcile_ca_truststore():
            self.charm.workload.restart()

    def reconcile_ca_truststore(self) -> bool:
        """Reconcile the JVM default truststore with the OAuth CAs.

        Returns:
            True if the truststore was modified.
        """
        workload = self.charm.workload
        if not workload.installed:
            logger.debug("Workload not installed yet, skipping truststore reconcile")
            return False

        if not (workload.root / workload.paths.java_truststore).exists():
            # Copy the local cacerts truststore into a writable truststore, then set the
            # app password on it so it matches the one the service is started with.
            workload.exec(
                command=[
                    "cp",
                    workload.paths.java_cacerts,
                    workload.paths.java_truststore,
                ]
            )
            self.charm.tls_manager.set_truststore_password(
                keystore=workload.paths.java_truststore,
                old_password=JAVA_CACERTS_DEFAULT_PASSWORD,
                new_password=workload.java_truststore_password,
            )

        certificates = self.cert_transfer.get_all_certificates()
        return self.charm.tls_manager.set_oauth_truststore(certificates)

    def reconcile_client_config(self) -> None:
        """Re-issue the OAuth client config once the unit address is known."""
        if not (self.charm.unit.is_leader() and self.charm.context.oauth_relation):
            return

        if not self.charm.context.unit.internal_address:
            logger.debug("Unit address not available yet, deferring client config")
            return

        redirect_uri = f"{self.charm.context.ingress_url}/login/oauth2/code/iam"
        self.oauth.update_client_config(
            ClientConfig(
                audience=["kafka"],
                redirect_uri=redirect_uri,
                scope="openid profile email phone offline address",
                grant_types=["authorization_code"],
            )
        )
