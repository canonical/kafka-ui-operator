#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for `TLSManager`, driven directly rather than through Scenario."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from core.models import Context, TLSContext
from core.workload import Paths, WorkloadBase
from literals import OAUTH_CA_ALIAS_PREFIX, SUBSTRATE
from managers.tls import TLSManager

from .helpers import TLSArtifacts, generate_tls_artifacts

logger = logging.getLogger(__name__)

INTERNAL_ADDRESS = "10.10.10.10"
BIND_ADDRESS = "10.20.20.20"
UNIT_NAME = "kafka-ui/0"
TRUSTSTORE_PASSWORD = "truststore-pass"


def keytool_listing(*aliases: str) -> str:
    """Render a `keytool -list` block the way `get_trusted_certificates` parses it."""
    # SHA-256 in keytool format: 32 colon-separated hex bytes -> 95 characters.
    fingerprint = ":".join(f"{index % 256:02X}" for index in range(32))

    return "\n".join(
        f"{alias}, 12 Jan 2026, trustedCertEntry, \n"
        f"Certificate fingerprint (SHA-256): {fingerprint}\n"
        for alias in aliases
    )


def openssl_sans(sans_dns: list[str], sans_ip: list[str]) -> str:
    """Render the `openssl x509 -ext subjectAltName` block `get_current_sans` parses."""
    entries = [f"DNS:{name}" for name in sans_dns] + [
        f"IP Address:{address}" for address in sans_ip
    ]

    return "X509v3 Subject Alternative Name: \n" + ", ".join(entries) + "\n"


@pytest.fixture()
def workload() -> MagicMock:
    """Build a fully mocked workload, with the charm's real `Paths`.

    `root` is only annotated on `WorkloadBase`, never assigned, so it is absent
    from the spec and has to be attached by hand. It defaults to reporting that
    every path exists, which is the interesting case for the truststore code.
    """
    mock = MagicMock(spec=WorkloadBase)
    mock.paths = Paths()
    mock.java_truststore_password = TRUSTSTORE_PASSWORD
    mock.exec.return_value = ""
    mock.root = MagicMock()
    mock.root.__truediv__.return_value.exists.return_value = True
    return mock


@pytest.fixture()
def context() -> MagicMock:
    """Build a mocked charm context with realistic addressing."""
    mock = MagicMock(spec=Context)
    mock.bind_address = BIND_ADDRESS
    mock.unit.internal_address = INTERNAL_ADDRESS
    mock.unit.unit.name = UNIT_NAME
    mock.unit.tls.truststore_password = TRUSTSTORE_PASSWORD
    return mock


@pytest.fixture()
def tls_manager(context: MagicMock, workload: MagicMock) -> TLSManager:
    return TLSManager(context=context, workload=workload, substrate=SUBSTRATE)


def test_generate_alias_is_relation_scoped(tls_manager: TLSManager) -> None:
    """Checks truststore aliases for related apps are unique per relation."""
    # Given / When / Then
    assert tls_manager.generate_alias("kafka", 7) == "kafka-7"
    assert tls_manager.generate_alias("kafka", 7) != tls_manager.generate_alias("kafka", 8)


def test_oauth_ca_alias_is_content_derived(tls_artifacts: TLSArtifacts) -> None:
    """Checks OAuth CA aliases are stable per certificate and unique between certificates."""
    # Given
    other = generate_tls_artifacts()

    # When
    alias = TLSManager.oauth_ca_alias(tls_artifacts.ca)

    # Then
    assert alias == TLSManager.oauth_ca_alias(tls_artifacts.ca)
    assert alias.startswith(OAUTH_CA_ALIAS_PREFIX)
    assert len(alias) == len(OAUTH_CA_ALIAS_PREFIX) + 16
    assert alias != TLSManager.oauth_ca_alias(other.ca)


def test_certificate_fingerprint_is_stable(tls_artifacts: TLSArtifacts) -> None:
    """Checks a certificate always hashes to the same fingerprint."""
    # Given
    other = generate_tls_artifacts()

    # When / Then
    assert TLSManager.certificate_fingerprint(
        tls_artifacts.ca
    ) == TLSManager.certificate_fingerprint(tls_artifacts.ca)
    assert TLSManager.certificate_fingerprint(
        tls_artifacts.ca
    ) != TLSManager.certificate_fingerprint(other.ca)


def test_keytool_hash_to_bytes() -> None:
    """Checks the colon-separated keytool hash format is decoded to raw bytes."""
    # Given / When / Then
    assert TLSManager.keytool_hash_to_bytes("00:0F:A0:FF") == bytes([0, 15, 160, 255])


def test_get_trusted_certificates_parses_keytool_output(
    tls_manager: TLSManager, workload: MagicMock
) -> None:
    """Checks every `trustedCertEntry` in a keytool listing is mapped to its fingerprint."""
    # Given
    workload.exec.return_value = keytool_listing("oauth-ca-abcdef0123456789", "kafka-7")

    # When
    trusted = tls_manager.get_trusted_certificates("truststore.jks", storepass="pass")

    # Then
    assert set(trusted) == {"oauth-ca-abcdef0123456789", "kafka-7"}
    assert all(isinstance(fingerprint, bytes) for fingerprint in trusted.values())


def test_get_trusted_certificates_without_a_truststore(tls_manager: TLSManager) -> None:
    """Checks a missing truststore yields nothing rather than invoking keytool."""
    # Given
    tls_manager.workload.root.__truediv__.return_value.exists.return_value = False

    # When / Then
    assert tls_manager.get_trusted_certificates("truststore.jks") == {}


def test_set_oauth_truststore_imports_new_cas(
    tls_manager: TLSManager, workload: MagicMock, tls_artifacts: TLSArtifacts
) -> None:
    """Checks a newly transferred CA is imported and reported as a change."""
    # Given -- an empty truststore
    workload.exec.return_value = ""

    # When
    with (
        patch.object(tls_manager, "import_cert") as patched_import,
        patch.object(tls_manager, "remove_cert") as patched_remove,
    ):
        changed = tls_manager.set_oauth_truststore({tls_artifacts.ca})

    # Then
    assert changed
    patched_remove.assert_not_called()
    patched_import.assert_called_once()
    assert patched_import.call_args.kwargs["alias"] == TLSManager.oauth_ca_alias(tls_artifacts.ca)
    assert patched_import.call_args.kwargs["cert_content"] == tls_artifacts.ca
    assert patched_import.call_args.kwargs["storepass"] == TRUSTSTORE_PASSWORD


def test_set_oauth_truststore_is_idempotent(
    tls_manager: TLSManager, workload: MagicMock, tls_artifacts: TLSArtifacts
) -> None:
    """Checks an already-trusted CA is neither re-imported nor reported as a change."""
    # Given -- the truststore already holds exactly this CA
    workload.exec.return_value = keytool_listing(TLSManager.oauth_ca_alias(tls_artifacts.ca))

    # When
    with (
        patch.object(tls_manager, "import_cert") as patched_import,
        patch.object(tls_manager, "remove_cert") as patched_remove,
    ):
        changed = tls_manager.set_oauth_truststore({tls_artifacts.ca})

    # Then
    assert not changed
    patched_import.assert_not_called()
    patched_remove.assert_not_called()


def test_set_oauth_truststore_removes_stale_cas(
    tls_manager: TLSManager, workload: MagicMock, tls_artifacts: TLSArtifacts
) -> None:
    """Checks a CA that is no longer transferred is dropped from the truststore."""
    # Given -- the truststore holds a CA the provider no longer sends
    stale = generate_tls_artifacts()
    stale_alias = TLSManager.oauth_ca_alias(stale.ca)
    workload.exec.return_value = keytool_listing(stale_alias)

    # When
    with (
        patch.object(tls_manager, "import_cert") as patched_import,
        patch.object(tls_manager, "remove_cert") as patched_remove,
    ):
        changed = tls_manager.set_oauth_truststore({tls_artifacts.ca})

    # Then
    assert changed
    patched_remove.assert_called_once()
    assert patched_remove.call_args.args[0] == stale_alias
    patched_import.assert_called_once()


def test_set_oauth_truststore_ignores_non_oauth_aliases(
    tls_manager: TLSManager, workload: MagicMock, tls_artifacts: TLSArtifacts
) -> None:
    """Checks client CAs sharing the truststore are never removed as stale OAuth CAs."""
    # Given -- a client CA alias alongside the OAuth one
    workload.exec.return_value = keytool_listing(
        "kafka-client-7", TLSManager.oauth_ca_alias(tls_artifacts.ca)
    )

    # When
    with patch.object(tls_manager, "remove_cert") as patched_remove:
        changed = tls_manager.set_oauth_truststore({tls_artifacts.ca})

    # Then
    assert not changed
    patched_remove.assert_not_called()


def test_set_oauth_truststore_fixes_ownership(
    tls_manager: TLSManager, workload: MagicMock, tls_artifacts: TLSArtifacts
) -> None:
    """Checks the truststore is left owned by, and readable to, the workload user."""
    # Given / When
    with patch.object(tls_manager, "import_cert"):
        tls_manager.set_oauth_truststore({tls_artifacts.ca})

    # Then
    commands = [
        call.args[0] if call.args else call.kwargs.get("command")
        for call in workload.exec.call_args_list
    ]
    assert ["chmod", "770", workload.paths.java_truststore] in commands
    assert any(command[0] == "chown" for command in commands if command)


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="VM SANs are built from the unit's own address")
def test_build_sans_on_vm(tls_manager: TLSManager) -> None:
    """Checks the machine charm requests a cert for its unit address and name."""
    # Given / When
    sans = tls_manager.build_sans()

    # Then
    assert sans.sans_ip == [INTERNAL_ADDRESS]
    assert UNIT_NAME in sans.sans_dns


@pytest.mark.skipif(SUBSTRATE == "vm", reason="K8s SANs are built from the pod's service names")
def test_build_sans_on_k8s(tls_manager: TLSManager) -> None:
    """Checks the K8s charm requests a cert covering its bind address and service names."""
    # Given
    tls_manager.unit_context.internal_address = "kafka-ui-k8s-0.kafka-ui-k8s-endpoints"

    # When
    sans = tls_manager.build_sans()

    # Then
    assert sans.sans_ip == [BIND_ADDRESS]
    assert "kafka-ui-k8s-0" in sans.sans_dns
    assert "kafka-ui-k8s-0.kafka-ui-k8s-endpoints" in sans.sans_dns
    assert sans.sans_dns == sorted(sans.sans_dns)


def test_get_current_sans_parses_openssl_output(
    tls_manager: TLSManager, workload: MagicMock
) -> None:
    """Checks the SANs on the unit's current certificate are read back and sorted."""
    # Given
    workload.exec.return_value = openssl_sans(
        ["kafka-ui/0", "some.host"], ["10.10.10.10", "10.20.20.20"]
    )

    # When
    sans = tls_manager.get_current_sans()

    # Then
    assert sans is not None
    assert sans.sans_dns == ["kafka-ui/0", "some.host"]
    assert sans.sans_ip == ["10.10.10.10", "10.20.20.20"]


def test_get_current_sans_without_a_certificate(tls_manager: TLSManager) -> None:
    """Checks nothing is read back before the unit holds a certificate."""
    # Given
    tls_manager.tls_context.certificate = ""

    # When / Then
    assert tls_manager.get_current_sans() is None


def test_sans_change_detected_is_false_without_tls(tls_manager: TLSManager) -> None:
    """Checks SANs are not compared before the unit has a full TLS identity."""
    # Given
    tls_manager.tls_context.ready = False

    # When / Then
    assert not tls_manager.sans_change_detected


def test_sans_change_detected_is_false_when_unchanged(
    tls_manager: TLSManager, workload: MagicMock
) -> None:
    """Checks a certificate already covering the unit's addresses needs no reissue."""
    # Given -- the certificate carries exactly the SANs the charm would ask for
    expected = tls_manager.build_sans()
    workload.exec.return_value = openssl_sans(expected.sans_dns, expected.sans_ip)

    # When / Then
    assert not tls_manager.sans_change_detected


@pytest.mark.parametrize("moved", ["ip", "dns"])
def test_sans_change_detected_when_the_unit_moves(
    tls_manager: TLSManager, workload: MagicMock, moved: str
) -> None:
    """Checks a certificate that no longer covers the unit's address is flagged for reissue."""
    # Given -- the unit has been rescheduled onto a new address or name
    expected = tls_manager.build_sans()
    sans_dns = ["somewhere.else"] if moved == "dns" else expected.sans_dns
    sans_ip = ["10.99.99.99"] if moved == "ip" else expected.sans_ip
    workload.exec.return_value = openssl_sans(sans_dns, sans_ip)

    # When / Then
    assert tls_manager.sans_change_detected


def test_bundle_is_ordered_and_deduplicated(tls_artifacts: TLSArtifacts) -> None:
    """Checks the cert bundle leads with the unit cert and never repeats an entry."""
    # Given
    tls_context = MagicMock(spec=TLSContext)
    tls_context.certificate = tls_artifacts.certificate
    tls_context.ca = tls_artifacts.ca
    tls_context.chain = [tls_artifacts.certificate, tls_artifacts.ca]

    # When
    bundle = TLSContext.bundle.fget(tls_context)

    # Then
    assert bundle[0] == tls_artifacts.certificate
    assert bundle[1] == tls_artifacts.ca
    assert len(bundle) == len(set(bundle))


def test_bundle_is_empty_without_a_full_identity() -> None:
    """Checks a partial TLS identity produces no bundle at all."""
    # Given
    tls_context = MagicMock(spec=TLSContext)
    tls_context.certificate = "a-certificate"
    tls_context.ca = ""

    # When / Then
    assert TLSContext.bundle.fget(tls_context) == []


def test_truststore_changed_tracks_client_cas(
    tls_manager: TLSManager, context: MagicMock, workload: MagicMock, tls_artifacts: TLSArtifacts
) -> None:
    """Checks a client CA that is not yet trusted is reported as a truststore change."""
    # Given -- an empty truststore and a Kafka client offering a CA
    workload.exec.return_value = ""
    context.kafka_client.tls_ca = tls_artifacts.ca
    context.kafka_connect_client.tls_ca = ""
    context.karapace_client.tls_ca = ""

    # When / Then
    assert tls_manager.truststore_changed()

    # ... and once that CA is trusted, nothing changes any more
    workload.exec.return_value = keytool_listing("kafka-client-7").replace(
        ":".join(f"{index % 256:02X}" for index in range(32)),
        ":".join(f"{byte:02X}" for byte in TLSManager.certificate_fingerprint(tls_artifacts.ca)),
    )
    assert not tls_manager.truststore_changed()
