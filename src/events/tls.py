#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for TLS/mTLS events."""

import json
import logging
from typing import TYPE_CHECKING

from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateAvailableEvent,
    CertificateRequestAttributes,
    PrivateKey,
    TLSCertificatesRequiresV4,
)
from ops.charm import (
    RelationBrokenEvent,
    RelationCreatedEvent,
)
from ops.framework import EventBase, EventSource, Object

from core.models import TLSContext
from literals import SUBSTRATE, TLS_REL

if TYPE_CHECKING:
    from charm import KafkaUiCharm

logger = logging.getLogger(__name__)


class RefreshTLSCertificatesEvent(EventBase):
    """Event for refreshing TLS certificates."""


class TLSHandler(Object):
    """Handler for managing the client and unit TLS keys/certs."""

    refresh_tls_certificates = EventSource(RefreshTLSCertificatesEvent)

    def __init__(self, charm: "KafkaUiCharm") -> None:
        super().__init__(charm, "tls")
        self.charm: "KafkaUiCharm" = charm

        self.sans = self.charm.tls_manager.build_sans()
        self.common_name = f"{self.charm.unit.name}-{self.charm.model.uuid}"

        private_key = None

        if key := self.charm.context.unit.tls.private_key:
            private_key = PrivateKey.from_string(key)

        self.certificates = TLSCertificatesRequiresV4(
            self.charm,
            TLS_REL,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=self.common_name,
                    sans_ip=frozenset(self.sans.sans_ip),
                    sans_dns=frozenset(self.sans.sans_dns),
                ),
            ],
            refresh_events=[self.refresh_tls_certificates],
            private_key=private_key,
        )

        self.framework.observe(self.charm.on[TLS_REL].relation_created, self._tls_relation_created)
        self.framework.observe(self.charm.on[TLS_REL].relation_broken, self._tls_relation_broken)

        self.framework.observe(
            getattr(self.certificates.on, "certificate_available"),
            self._on_certificate_available,
        )

    def init_unit_tls(self) -> None:
        """Initialize Unit TLS. Safe to call anywhere.

        Generates keystore/truststore passwords, a temporary internal CA,
        and self-signed certificate bundle.
        """
        if self.charm.context.unit.tls.ready:
            return

        self.charm.context.unit.update(
            {
                TLSContext.KEYSTORE_PASSWORD: self.charm.context.unit.tls.keystore_password
                or self.charm.workload.generate_password(),
                TLSContext.TRUSTSTORE_PASSWORD: self.charm.context.unit.tls.truststore_password
                or self.charm.workload.generate_password(),
            }
        )

        if SUBSTRATE == "k8s":
            return

        # Generate a self-singend certificate
        self_signed = self.charm.tls_manager.generate_self_signed_certificate()
        self.charm.context.unit.update(
            {
                TLSContext.PRIVATE_KEY: self_signed.private_key,
                TLSContext.CERT: self_signed.certificate,
                TLSContext.CSR: self_signed.csr,
                TLSContext.CA: self_signed.ca,
            }
        )
        self.charm.tls_manager.configure()

    def _tls_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle `certificates_relation_created` event."""
        if not self.healthy:
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        self.charm.context.app.update({"tls": "enabled"})

    def _tls_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle `certificates_relation_broken` event."""
        # clear TLS state
        self.charm.context.unit.update(
            {
                TLSContext.CERT: "",
                TLSContext.CSR: "",
                TLSContext.CA: "",
                TLSContext.CHAIN: "",
            }
        )

        # remove all existing keystores from the unit so we don't preserve certs
        self.charm.tls_manager.remove_stores()

        if not self.charm.unit.is_leader():
            return

        self.charm.context.app.update({"tls": ""})
        # This config-changed will switch to internal TLS.
        self.charm.on.config_changed.emit()

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        """Handle TLS `certificate_available` event."""
        if not self.healthy:
            event.defer()
            return

        certificate = event.certificate.raw
        ca = event.ca.raw
        chain = json.dumps([certificate.raw for certificate in event.chain])

        self.charm.context.unit.update(
            {
                TLSContext.CERT: certificate,
                TLSContext.CHAIN: chain,
                TLSContext.CA: ca,
            }
        )

        self.charm.tls_manager.remove_stores()
        self.charm.tls_manager.configure()
        self.charm.on.config_changed.emit()

    @property
    def healthy(self) -> bool:
        """Return True if workload and Juju setup is complete, False otherwise."""
        if not all([self.charm.workload.container_can_connect, self.charm.workload.installed]):
            logger.debug("Workload not ready yet.")
            return False

        if not self.charm.context.peer_relation:
            logger.warning("No peer relation on TLS handler.")
            return False

        return True
