#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import json
import logging
from typing import cast
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from charms.tls_certificates_interface.v4.tls_certificates import (
    Certificate,
    CertificateAvailableEvent,
    CertificateSigningRequest,
    PrivateKey,
)
from ops.testing import Context, PeerRelation, Relation, State

from core.models import TLSContext
from literals import PEER_REL, SUBSTRATE, TLS_REL, Status

from .helpers import KafkaUiCharm, TLSArtifacts, secret_contents, stored_peer_data

logger = logging.getLogger(__name__)


def certificate_available_event(artifacts: TLSArtifacts, chain: list[str]):
    """Build the event the `tls-certificates` requirer emits once a cert is issued."""
    return CertificateAvailableEvent(
        handle=MagicMock(),
        certificate=Certificate.from_string(artifacts.certificate),
        certificate_signing_request=CertificateSigningRequest.from_string(artifacts.csr),
        ca=Certificate.from_string(artifacts.ca),
        chain=[Certificate.from_string(certificate) for certificate in chain],
    )


@pytest.mark.parametrize("is_leader", [True, False])
def test_tls_relation_created_enables_tls(
    ctx: Context, base_state: State, peer_rel: PeerRelation, is_leader: bool
) -> None:
    """Checks `certificates-relation-created` flags TLS on app data, on the leader only."""
    # Given
    tls_rel = Relation(TLS_REL, TLS_REL)
    state_in = dataclasses.replace(base_state, relations=[peer_rel, tls_rel], leader=is_leader)

    # When
    state_out = ctx.run(ctx.on.relation_created(tls_rel), state_in)

    # Then
    peer_out = state_out.get_relation(peer_rel.id)
    if is_leader:
        assert peer_out.local_app_data.get("tls") == "enabled"
    else:
        assert not peer_out.local_app_data.get("tls")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "TLSHandler.__init__ builds its SANs before any guard runs, and without the peer "
        "relation `internal_address` is empty, so TLSCertificatesRequiresV4 rejects the "
        "empty SAN IP. The `healthy` check in _tls_relation_created is therefore dead code. "
        "Unreachable in practice -- Juju creates the peer relation first -- but the guard "
        "should either work or go. Remove this marker once it does."
    ),
)
def test_tls_relation_created_defers_without_peer_relation(
    ctx: Context, base_state: State
) -> None:
    """Checks the handler waits for the peer relation before touching app data."""
    # Given
    tls_rel = Relation(TLS_REL, TLS_REL)
    state_in = dataclasses.replace(base_state, relations=[tls_rel])

    # When
    state_out = ctx.run(ctx.on.relation_created(tls_rel), state_in)

    # Then
    assert [event.name for event in state_out.deferred].count("certificates_relation_created") == 1


@pytest.mark.parametrize("is_leader", [True, False])
def test_tls_relation_broken_drops_the_issued_certificate(
    ctx: Context, base_state: State, intermediate_tls_artifacts: TLSArtifacts, is_leader: bool
) -> None:
    """Checks `certificates-relation-broken` discards the provider's material and the stores.

    On the leader the follow-up `config-changed` immediately re-provisions internal
    TLS (on VM), so the unit ends up holding a *different*, self-signed bundle rather
    than no bundle at all -- which is the documented intent of the handler.
    """
    # Given -- the provider-issued bundle is distinct from the internal self-signed one
    issued = intermediate_tls_artifacts
    peer_rel = PeerRelation(
        PEER_REL,
        PEER_REL,
        local_app_data={"tls": "enabled"},
        local_unit_data={
            TLSContext.CA: issued.ca,
            TLSContext.CERT: issued.certificate,
            TLSContext.CSR: issued.csr,
            TLSContext.CHAIN: json.dumps(issued.chain),
        },
    )
    tls_rel = Relation(TLS_REL, TLS_REL)
    state_in = dataclasses.replace(base_state, relations=[peer_rel, tls_rel], leader=is_leader)

    # When
    with patch("managers.tls.TLSManager.remove_stores") as patched_remove_stores:
        state_out = ctx.run(ctx.on.relation_broken(tls_rel), state_in)

    # Then
    patched_remove_stores.assert_called_once()

    stored = stored_peer_data(state_out, peer_rel.id)
    assert stored.get(TLSContext.CERT) != issued.certificate
    assert not stored.get(TLSContext.CHAIN)

    peer_out = state_out.get_relation(peer_rel.id)
    if is_leader:
        assert not peer_out.local_app_data.get("tls")
    else:
        assert peer_out.local_app_data.get("tls") == "enabled"
        assert not stored.get(TLSContext.CERT)
        assert not stored.get(TLSContext.CA)


def test_init_unit_tls_generates_store_passwords(ctx: Context, base_state: State) -> None:
    """Checks keystore and truststore passwords are created and kept in Juju secrets."""
    # Given
    state_in = base_state

    # When
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Then
    stored = secret_contents(state_out)
    assert stored.get(TLSContext.KEYSTORE_PASSWORD)
    assert stored.get(TLSContext.TRUSTSTORE_PASSWORD)


def test_init_unit_tls_keeps_existing_store_passwords(ctx: Context, base_state: State) -> None:
    """Checks existing store passwords are never rotated by a later reconcile."""
    # Given
    state_in = base_state

    # When
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    first = secret_contents(state_out)
    state_out = ctx.run(ctx.on.config_changed(), state_out)
    second = secret_contents(state_out)

    # Then
    assert first[TLSContext.KEYSTORE_PASSWORD] == second[TLSContext.KEYSTORE_PASSWORD]
    assert first[TLSContext.TRUSTSTORE_PASSWORD] == second[TLSContext.TRUSTSTORE_PASSWORD]


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="self-signed certs are only issued on VM")
def test_init_unit_tls_issues_self_signed_certificate_on_vm(
    ctx: Context, base_state: State, patched_self_signed_certificate
) -> None:
    """Checks the machine charm falls back to an internally signed certificate."""
    # Given
    state_in = base_state

    # When
    with patch("managers.tls.TLSManager.configure") as patched_configure:
        state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched_self_signed_certificate.assert_called_once()
    patched_configure.assert_called_once()

    stored = secret_contents(state_out)
    assert stored.get(TLSContext.PRIVATE_KEY)
    assert stored.get(TLSContext.CERT)
    assert stored.get(TLSContext.CSR)


@pytest.mark.skipif(SUBSTRATE == "vm", reason="K8s terminates TLS at the ingress")
def test_init_unit_tls_skips_self_signed_certificate_on_k8s(
    ctx: Context, base_state: State, patched_self_signed_certificate
) -> None:
    """Checks the K8s charm stops at the store passwords and issues no certificate."""
    # Given
    state_in = base_state

    # When
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched_self_signed_certificate.assert_not_called()

    assert not secret_contents(state_out).get(TLSContext.CERT)


@pytest.mark.parametrize("with_chain", [False, True])
def test_certificate_available_updates_unit_data(
    ctx: Context,
    base_state: State,
    tls_artifacts: TLSArtifacts,
    intermediate_tls_artifacts: TLSArtifacts,
    with_chain: bool,
) -> None:
    """Checks an issued certificate lands on unit data and reconfigures the stores."""
    # Given
    artifacts = intermediate_tls_artifacts if with_chain else tls_artifacts
    chain = artifacts.chain if with_chain else []

    peer_rel = PeerRelation(
        PEER_REL,
        PEER_REL,
        local_app_data={"tls": "enabled"},
        local_unit_data={TLSContext.CSR: artifacts.csr},
    )
    tls_rel = Relation(TLS_REL, TLS_REL)
    state_in = dataclasses.replace(base_state, relations=[peer_rel, tls_rel])

    # When
    with (
        ctx(ctx.on.update_status(), state_in) as manager,
        patch("managers.tls.TLSManager.configure") as patched_configure,
        patch("managers.tls.TLSManager.remove_stores") as patched_remove_stores,
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates."
            "TLSCertificatesRequiresV4.private_key",
            new_callable=PropertyMock,
            return_value=PrivateKey.from_string(artifacts.private_key),
        ),
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        charm.tls._on_certificate_available(certificate_available_event(artifacts, chain))
        state_out = manager.run()

    # Then
    patched_configure.assert_called()
    patched_remove_stores.assert_called()

    stored = stored_peer_data(state_out, peer_rel.id)

    assert stored.get(TLSContext.CERT) == artifacts.certificate
    assert stored.get(TLSContext.PRIVATE_KEY) == artifacts.private_key
    assert stored.get(TLSContext.CA) == artifacts.ca
    assert json.loads(stored.get(TLSContext.CHAIN, "[]")) == chain


def test_certificate_available_defers_without_peer_relation(
    ctx: Context, base_state: State, tls_artifacts: TLSArtifacts
) -> None:
    """Checks an issued certificate is not applied before the peer relation exists."""
    # Given
    tls_rel = Relation(TLS_REL, TLS_REL)
    state_in = dataclasses.replace(base_state, relations=[tls_rel])

    # When
    with (
        ctx(ctx.on.update_status(), state_in) as manager,
        patch("managers.tls.TLSManager.configure") as patched_configure,
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        charm.tls._on_certificate_available(certificate_available_event(tls_artifacts, []))
        manager.run()

    # Then
    patched_configure.assert_not_called()


def test_tls_ready_switches_endpoint_scheme(
    ctx: Context, base_state: State, tls_artifacts: TLSArtifacts
) -> None:
    """Checks a unit holding a full TLS bundle advertises an endpoint accordingly."""
    # Given
    peer_rel = PeerRelation(
        PEER_REL,
        PEER_REL,
        local_app_data={"tls": "enabled"},
        local_unit_data={
            TLSContext.CA: tls_artifacts.ca,
            TLSContext.CERT: tls_artifacts.certificate,
            TLSContext.PRIVATE_KEY: tls_artifacts.private_key,
        },
    )
    state_in = dataclasses.replace(base_state, relations=[peer_rel])

    # When
    with ctx(ctx.on.update_status(), state_in) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        endpoint = charm.context.endpoint
        tls_ready = charm.context.unit.tls.ready
        manager.run()

    # Then
    assert tls_ready
    assert endpoint.startswith("https://")
    assert charm.context.unit.tls.status == Status.ACTIVE
