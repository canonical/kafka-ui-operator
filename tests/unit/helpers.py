#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared helpers for the Kafka UI unit test suite.

This suite is kept identical between `kafka-ui-operator` (VM) and
`kafka-ui-k8s-operator` (K8s). Neither repo sets `SUBSTRATE` from the
environment the way the `kafka-operator` monorepo does -- each charm hard-codes
it in its own `literals` module -- so substrate-specific behaviour is gated by
importing it from there.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import yaml
from charms.tls_certificates_interface.v4.tls_certificates import (
    generate_ca,
    generate_certificate,
    generate_csr,
    generate_private_key,
)

from charm import KafkaUiCharm
from literals import SUBSTRATE

__all__ = [
    "ACTIONS",
    "CHARM_KEY",
    "CONFIG",
    "CONFIG_DEFAULTS",
    "METADATA",
    "SUBSTRATE",
    "KafkaUiCharm",
    "TLSArtifacts",
    "generate_tls_artifacts",
    "secret_contents",
    "stored_peer_data",
]

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CONFIG = yaml.safe_load(Path("./config.yaml").read_text())
# Kafka UI declares no actions; kept for symmetry with the other charm suites.
ACTIONS: dict = {}

CHARM_KEY = METADATA["name"]

CONFIG_DEFAULTS = {
    option.replace("-", "_"): spec["default"]
    for option, spec in CONFIG["options"].items()
    if "default" in spec
}


def secret_contents(state) -> dict[str, str]:
    """Flatten every Juju secret in a `State` into one mapping."""
    return {key: value for secret in state.secrets for key, value in secret.latest_content.items()}


def stored_peer_data(state, relation_id: int) -> dict[str, str]:
    """Return everything the unit has stored on its peer relation.

    `DataPeerUnitData` promotes a field to a Juju secret only when it is not
    already present in the plain databag, so a value seeded through
    `local_unit_data` stays in the databag while the same field written from
    scratch lands in a secret. Tests care that the value is stored, not where.
    """
    relation = state.get_relation(relation_id)

    return dict(relation.local_unit_data) | dict(relation.local_app_data) | secret_contents(state)


@dataclass
class TLSArtifacts:
    """A complete set of TLS material for a single unit."""

    certificate: str
    private_key: str
    ca: str
    csr: str
    chain: list[str] = field(default_factory=list)


def generate_tls_artifacts(
    common_name: str = "kafka-ui/0",
    sans_dns: list[str] | None = None,
    sans_ip: list[str] | None = None,
    with_intermediate: bool = False,
) -> TLSArtifacts:
    """Generate the TLS artifacts a unit would receive from a certificates provider.

    Args:
        common_name: certificate subject common name.
        sans_dns: SAN DNS entries. Defaults to `["localhost"]`.
        sans_ip: SAN IP entries. Defaults to `["127.0.0.1"]`.
        with_intermediate: whether an intermediate CA signs the end certificate,
            producing a chain longer than the certificate plus root CA.

    Returns:
        TLSArtifacts: the generated material, PEM-encoded.
    """
    sans_dns = ["localhost"] if sans_dns is None else sans_dns
    sans_ip = ["127.0.0.1"] if sans_ip is None else sans_ip
    validity = timedelta(days=365)

    ca_key = generate_private_key()
    ca = generate_ca(private_key=ca_key, validity=validity, common_name="kafka-ui-test-ca")
    signing_cert, signing_key = ca, ca_key

    if with_intermediate:
        intermediate_key = generate_private_key()
        intermediate_csr = generate_csr(
            private_key=intermediate_key, common_name="kafka-ui-test-intermediate"
        )
        signing_cert = generate_certificate(
            csr=intermediate_csr,
            ca=ca,
            ca_private_key=ca_key,
            validity=validity,
            is_ca=True,
        )
        signing_key = intermediate_key

    private_key = generate_private_key()
    csr = generate_csr(
        private_key=private_key,
        common_name=common_name,
        sans_dns=frozenset(sans_dns),
        sans_ip=frozenset(sans_ip),
    )
    certificate = generate_certificate(
        csr=csr, ca=signing_cert, ca_private_key=signing_key, validity=validity
    )

    # de-duplicated, ordering preserved -- `TLSContext.bundle` relies on both
    chain = list(dict.fromkeys([certificate.raw, signing_cert.raw, ca.raw]))

    return TLSArtifacts(
        certificate=certificate.raw,
        private_key=private_key.raw,
        ca=ca.raw,
        csr=csr.raw,
        chain=chain,
    )
