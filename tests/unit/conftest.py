#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures shared by the Kafka UI unit test suite.

The autouse fixtures below neutralise everything that would otherwise reach out
of the test process -- snapd, the Pebble container, the filesystem, the network
and real key generation. Tests opt *in* to real behaviour by re-patching the
specific target they care about, never by disabling a fixture.
"""

from collections import defaultdict
from contextlib import ExitStack
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest
from ops.testing import Container, Context, PeerRelation, State

from core.models import SelfSignedCertificate
from literals import CONTAINER, PEER_REL, SNAP_NAME, SUBSTRATE

from .helpers import CONFIG, METADATA, KafkaUiCharm, TLSArtifacts, generate_tls_artifacts

# Deterministic stand-in for the URL traefik would hand back on K8s.
INGRESS_URL = "http://ingress.test/test-kafka-ui-k8s"


# --------------------------------------------------------------------------- #
# TLS material                                                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def session_tls_artifacts() -> TLSArtifacts:
    """Generate TLS artifacts exactly once for the whole session.

    Key generation is the single most expensive thing this suite does, and the
    charm regenerates a self-signed bundle on every `config-changed` on VM.
    """
    return generate_tls_artifacts()


@pytest.fixture()
def tls_artifacts(session_tls_artifacts: TLSArtifacts) -> TLSArtifacts:
    """Return the session TLS artifacts, for tests that need to assert on them."""
    return session_tls_artifacts


@pytest.fixture()
def intermediate_tls_artifacts() -> TLSArtifacts:
    """TLS artifacts signed by an intermediate CA, giving a three-item chain."""
    return generate_tls_artifacts(with_intermediate=True)


# --------------------------------------------------------------------------- #
# Autouse isolation                                                            #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def patched_snap_cache():
    """Keep the machine workload away from the host's snapd."""
    if SUBSTRATE != "vm":
        yield
        return

    kafka_ui_snap = Mock()
    kafka_ui_snap.services = defaultdict(lambda: {"active": True})

    snap_cache = Mock(return_value={SNAP_NAME: kafka_ui_snap})

    with patch("workload.snap.SnapCache", snap_cache):
        yield kafka_ui_snap


@pytest.fixture(autouse=True)
def patched_workload():
    """Stub out every workload operation that touches the host or the container."""
    targets = [
        patch("workload.Workload.installed", True),
        patch("workload.Workload.container_can_connect", True),
        patch("workload.Workload.active", return_value=True),
        patch("workload.Workload.exec", return_value=""),
        patch("workload.Workload.write"),
        patch("workload.Workload.read", return_value=[]),
        patch("workload.Workload.set_environment"),
        patch("workload.Workload.start"),
        patch("workload.Workload.stop"),
        patch("workload.Workload.restart"),
    ]

    if SUBSTRATE == "vm":
        # `install` only exists on the machine workload.
        targets.append(patch("workload.Workload.install", return_value=True))

    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(target)
        yield


@pytest.fixture(autouse=True)
def patched_health_response():
    """Answer the `health_check` HTTP probe without touching the network.

    Left unpatched, every test that gets past the Kafka relation check spends
    ~8s on connection timeouts against Scenario's placeholder unit address.
    """
    response = MagicMock()
    response.status_code = 200

    with patch("charm.requests.get", return_value=response) as health_response:
        yield health_response


@pytest.fixture(autouse=True)
def patched_tenacity_sleep():
    """Collapse the `tenacity` retry backoff on `health_check` and `active`."""
    with patch("tenacity.nap.time") as nap:
        yield nap


@pytest.fixture(autouse=True)
def patched_self_signed_certificate(session_tls_artifacts: TLSArtifacts):
    """Serve pre-generated TLS material instead of generating a CA per event."""
    self_signed = SelfSignedCertificate(
        ca=session_tls_artifacts.ca,
        csr=session_tls_artifacts.csr,
        certificate=session_tls_artifacts.certificate,
        private_key=session_tls_artifacts.private_key,
    )

    with patch(
        "managers.tls.TLSManager.generate_self_signed_certificate", return_value=self_signed
    ) as generate:
        yield generate


@pytest.fixture(autouse=True)
def patched_ingress():
    """Report a ready ingress on K8s so tests can reach statuses beyond it."""
    if SUBSTRATE != "k8s":
        yield
        return

    with (
        patch(
            "charms.traefik_k8s.v2.ingress.IngressPerAppRequirer.is_ready", return_value=True
        ) as is_ready,
        patch(
            "charms.traefik_k8s.v2.ingress.IngressPerAppRequirer.url",
            new_callable=PropertyMock,
            return_value=INGRESS_URL,
        ),
    ):
        yield is_ready


# --------------------------------------------------------------------------- #
# Scenario context and state                                                   #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def ctx() -> Context:
    return Context(KafkaUiCharm, meta=METADATA, config=CONFIG, unit_id=0)


@pytest.fixture()
def peer_rel() -> PeerRelation:
    return PeerRelation(PEER_REL, PEER_REL)


@pytest.fixture()
def base_state(peer_rel: PeerRelation) -> State:
    """Build a leader unit with its peer relation, plus a container on K8s."""
    if SUBSTRATE == "k8s":
        return State(
            leader=True,
            relations=[peer_rel],
            containers=[Container(name=CONTAINER, can_connect=True)],
        )

    return State(leader=True, relations=[peer_rel])


# --------------------------------------------------------------------------- #
# Relation payloads                                                            #
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def kafka_client_data() -> dict[str, str]:
    return {
        "username": "relation-6",
        "password": "mellon",
        "endpoints": "10.10.10.10:9092,10.10.10.11:9092",
        "topic": "__kafka-ui",
        "tls": "disabled",
    }


@pytest.fixture(scope="module")
def connect_client_data() -> dict[str, str]:
    return {
        "endpoints": "http://10.20.20.20:8083",
        "password": "mellon",
        "plugin-url": "http://10.20.20.20:8083/plugin",
        "tls": "disabled",
    }


@pytest.fixture(scope="module")
def karapace_client_data() -> dict[str, str]:
    return {
        "endpoints": "10.30.30.30:8081",
        "password": "mellon",
        "subject": "__kafka-ui",
        "tls": "disabled",
    }


@pytest.fixture(scope="module")
def oauth_data() -> dict[str, str]:
    """Build a provider databag satisfying the `oauth` interface schema.

    Every field below is required by the interface's provider schema, which the
    charm lib validates on `relation-changed`.
    """
    return {
        "client_id": "kafka-ui-client",
        "issuer_url": "https://hydra.test",
        "authorization_endpoint": "https://hydra.test/oauth2/auth",
        "token_endpoint": "https://hydra.test/oauth2/token",
        "introspection_endpoint": "https://hydra.test/admin/oauth2/introspect",
        "userinfo_endpoint": "https://hydra.test/userinfo",
        "jwks_endpoint": "https://hydra.test/.well-known/jwks.json",
        "scope": "openid profile email phone offline address",
        # the lib json-decodes databag values, so a lowercase "true" would arrive
        # as a bool and fail the schema, which requires a string
        "jwt_access_token": "True",
    }
