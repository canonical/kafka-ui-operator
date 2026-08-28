#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import json
import logging
from typing import cast

import pytest
import yaml
from ops.testing import Context, Relation, State

from literals import (
    ADMIN_ROLE,
    CHARMED_READ_ROLE,
    CLUSTER_NAME,
    KAFKA_CONNECT_REL,
    KAFKA_REL,
    KARAPACE_REL,
    OAUTH_REL,
    RBAC_SUBJECT_PROVIDER,
    ROLE_PERMISSIONS,
    SUBSTRATE,
)

from .helpers import KafkaUiCharm, TLSArtifacts

logger = logging.getLogger(__name__)


def config_manager(ctx: Context, state: State):
    """Run a no-op event and hand back the charm's `ConfigManager`."""
    with ctx(ctx.on.update_status(), state) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        manager.run()

    return charm.config_manager


@pytest.fixture()
def kafka_state(base_state: State, kafka_client_data: dict[str, str]) -> State:
    """Build a state related to Apache Kafka over a plaintext listener."""
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data=kafka_client_data)
    return dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])


def test_kafka_cluster_config_carries_credentials(
    ctx: Context, kafka_state: State, kafka_client_data: dict[str, str]
) -> None:
    """Checks the rendered cluster block carries the bootstrap servers and SASL credentials."""
    # Given / When
    cluster = config_manager(ctx, kafka_state).kafka_cluster_config["kafka"]["clusters"][0]

    # Then
    assert cluster["name"] == CLUSTER_NAME
    assert cluster["bootstrap-servers"] == kafka_client_data["endpoints"]

    properties = cluster["properties"]
    assert properties["security.protocol"] == "SASL_PLAINTEXT"
    assert properties["sasl.mechanism"] == "SCRAM-SHA-512"
    assert f'username="{kafka_client_data["username"]}"' in properties["sasl.jaas.config"]
    assert f'password="{kafka_client_data["password"]}"' in properties["sasl.jaas.config"]


def test_cluster_tls_properties_absent_without_tls(ctx: Context, kafka_state: State) -> None:
    """Checks no truststore is configured while the Kafka listener is plaintext."""
    # Given / When
    manager = config_manager(ctx, kafka_state)

    # Then
    assert manager.cluster_tls_properties == {}
    assert "ssl" not in manager.kafka_cluster_config["kafka"]["clusters"][0]


def test_cluster_tls_properties_present_with_tls(
    ctx: Context, base_state: State, kafka_client_data: dict[str, str], tls_artifacts: TLSArtifacts
) -> None:
    """Checks a TLS-enabled Kafka listener switches the protocol and adds the truststore."""
    # Given
    kafka_rel = Relation(
        KAFKA_REL,
        KAFKA_REL,
        remote_app_data=kafka_client_data | {"tls": "enabled", "tls-ca": tls_artifacts.ca},
    )
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])

    # When
    manager = config_manager(ctx, state_in)

    # Then
    assert manager.cluster_tls_properties["ssl"]["verify-ssl"] is True
    assert (
        manager.cluster_tls_properties["ssl"]["truststore-location"]
        == manager.workload.paths.truststore
    )
    assert manager.kafka_client_properties_config["security.protocol"] == "SASL_SSL"


def test_kafka_connect_config_absent_without_relation(ctx: Context, kafka_state: State) -> None:
    """Checks the Kafka Connect block is omitted when the charm is not related to Connect."""
    # Given / When
    manager = config_manager(ctx, kafka_state)

    # Then
    assert manager.kafka_connect_config is None
    assert "kafka-connect: null" not in manager.clean_yaml_config


def test_kafka_connect_config_present_with_relation(
    ctx: Context, kafka_state: State, connect_client_data: dict[str, str]
) -> None:
    """Checks relating to Kafka Connect adds a named connect cluster with credentials."""
    # Given
    connect_rel = Relation(
        KAFKA_CONNECT_REL, KAFKA_CONNECT_REL, remote_app_data=connect_client_data
    )
    state_in = dataclasses.replace(kafka_state, relations=[*kafka_state.relations, connect_rel])

    # When
    connect_config = config_manager(ctx, state_in).kafka_connect_config

    # Then
    assert connect_config is not None
    assert connect_config[0]["name"] == "kafka-connect"
    assert connect_config[0]["address"] == connect_client_data["endpoints"]
    assert connect_config[0]["username"] == f"relation-{connect_rel.id}"
    assert connect_config[0]["password"] == connect_client_data["password"]


def test_schema_registry_auth_absent_without_relation(ctx: Context, kafka_state: State) -> None:
    """Checks schema registry credentials are omitted without a Karapace relation."""
    # Given / When
    manager = config_manager(ctx, kafka_state)

    # Then
    assert manager.schema_registry_auth_config is None


def test_schema_registry_auth_present_with_relation(
    ctx: Context, kafka_state: State, karapace_client_data: dict[str, str]
) -> None:
    """Checks relating to Karapace adds schema registry credentials."""
    # Given
    karapace_rel = Relation(KARAPACE_REL, KARAPACE_REL, remote_app_data=karapace_client_data)
    state_in = dataclasses.replace(kafka_state, relations=[*kafka_state.relations, karapace_rel])

    # When
    manager = config_manager(ctx, state_in)

    # Then
    assert manager.schema_registry_auth_config == {
        "username": f"relation-{karapace_rel.id}",
        "password": karapace_client_data["password"],
    }
    cluster = manager.kafka_cluster_config["kafka"]["clusters"][0]
    assert cluster["schemaRegistry"] == f"http://{karapace_client_data['endpoints']}"


def test_auth_config_defaults_to_login_form(ctx: Context, kafka_state: State) -> None:
    """Checks the UI falls back to basic auth when no OAuth provider is related."""
    # Given / When
    manager = config_manager(ctx, kafka_state)

    # Then
    assert manager.auth_config == {"auth": {"type": "LOGIN_FORM"}}
    assert manager.spring_security_config["security"]["user"]["name"] == "admin"
    assert manager.spring_security_config["security"]["user"]["password"]
    assert manager.kafka_cluster_config["kafka"]["clusters"][0]["read-only"] is True


def test_auth_config_switches_to_oauth(
    ctx: Context, kafka_state: State, oauth_data: dict[str, str]
) -> None:
    """Checks relating an OAuth provider replaces basic auth with an OAUTH2 client."""
    # Given
    oauth_rel = Relation(OAUTH_REL, OAUTH_REL, remote_app_data=oauth_data)
    state_in = dataclasses.replace(kafka_state, relations=[*kafka_state.relations, oauth_rel])

    # When
    manager = config_manager(ctx, state_in)

    # Then
    client = manager.auth_config["auth"]["oauth2"]["client"]["iam"]
    assert manager.auth_config["auth"]["type"] == "OAUTH2"
    assert client["clientId"] == oauth_data["client_id"]
    assert client["issuer-uri"] == oauth_data["issuer_url"]
    assert client["custom-params"] == {"type": RBAC_SUBJECT_PROVIDER}
    assert client["redirect-uri"].endswith("/login/oauth2/code/iam")

    # basic auth credentials must not leak into the rendered config alongside OAuth
    assert manager.spring_security_config == {}
    # and the cluster becomes writable once users are authenticated
    assert manager.kafka_cluster_config["kafka"]["clusters"][0]["read-only"] is False


def test_rbac_config_absent_without_oauth(ctx: Context, kafka_state: State) -> None:
    """Checks RBAC roles are not rendered when authentication is basic auth."""
    # Given
    state_in = dataclasses.replace(
        kafka_state, config={"roles-mapping": json.dumps({"someone@test.com": ADMIN_ROLE})}
    )

    # When / Then
    assert config_manager(ctx, state_in).rbac_config == {}


def test_rbac_config_absent_with_empty_mapping(
    ctx: Context, kafka_state: State, oauth_data: dict[str, str]
) -> None:
    """Checks an empty `roles-mapping` renders no RBAC block even under OAuth."""
    # Given
    oauth_rel = Relation(OAUTH_REL, OAUTH_REL, remote_app_data=oauth_data)
    state_in = dataclasses.replace(
        kafka_state,
        relations=[*kafka_state.relations, oauth_rel],
        config={"roles-mapping": "{}"},
    )

    # When / Then
    assert config_manager(ctx, state_in).rbac_config == {}


def test_rbac_config_maps_users_to_roles(
    ctx: Context, kafka_state: State, oauth_data: dict[str, str]
) -> None:
    """Checks `roles-mapping` becomes one RBAC role per mapped role, with its subjects."""
    # Given
    mapping = {
        "admin@test.com": ADMIN_ROLE,
        "reader@test.com": CHARMED_READ_ROLE,
        "other-reader@test.com": CHARMED_READ_ROLE,
    }
    oauth_rel = Relation(OAUTH_REL, OAUTH_REL, remote_app_data=oauth_data)
    state_in = dataclasses.replace(
        kafka_state,
        relations=[*kafka_state.relations, oauth_rel],
        config={"roles-mapping": json.dumps(mapping)},
    )

    # When
    roles = config_manager(ctx, state_in).rbac_config["rbac"]["roles"]

    # Then -- only the two roles that actually have subjects are rendered
    assert {role["name"] for role in roles} == {ADMIN_ROLE, CHARMED_READ_ROLE}

    by_name = {role["name"]: role for role in roles}
    assert by_name[ADMIN_ROLE]["subjects"] == [
        {"provider": RBAC_SUBJECT_PROVIDER, "type": "user", "value": "admin@test.com"}
    ]
    assert {subject["value"] for subject in by_name[CHARMED_READ_ROLE]["subjects"]} == {
        "reader@test.com",
        "other-reader@test.com",
    }
    for name, role in by_name.items():
        assert role["clusters"] == [CLUSTER_NAME]
        assert role["permissions"] == ROLE_PERMISSIONS[name]


def test_monitoring_and_webclient_always_rendered(ctx: Context, kafka_state: State) -> None:
    """Checks the observability endpoints and buffer size are always configured."""
    # Given / When
    manager = config_manager(ctx, kafka_state)

    # Then
    endpoints = manager.monitoring_config["management"]["endpoints"]
    assert endpoints["web"]["exposure"]["include"] == "info,health,prometheus"
    assert manager.webclient_config["webclient"]["max-in-memory-buffer-size"] == "50MB"


def test_clean_yaml_config_strips_nulls_and_parses(ctx: Context, kafka_state: State) -> None:
    """Checks the written config is valid YAML with every `null` entry removed."""
    # Given / When
    manager = config_manager(ctx, kafka_state)
    rendered = manager.clean_yaml_config

    # Then
    assert "null" not in rendered
    parsed = yaml.safe_load(rendered)
    assert parsed["kafka"]["clusters"][0]["name"] == CLUSTER_NAME
    assert parsed["auth"]["type"] == "LOGIN_FORM"


def test_config_changed_detects_drift(ctx: Context, kafka_state: State) -> None:
    """Checks `config_changed` compares the rendered config against what is on disk."""
    # Given
    manager = config_manager(ctx, kafka_state)

    # When / Then -- the autouse `read` stub returns an empty file
    assert manager.config_changed()

    manager.workload.read.return_value = manager.clean_yaml_config.split("\n")
    assert not manager.config_changed()


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="the JVM truststore is set in the Pebble layer")
def test_java_opts_carry_truststore_on_vm(ctx: Context, kafka_state: State) -> None:
    """Checks the machine charm passes the truststore to the JVM through JAVA_OPTS."""
    # Given / When
    manager = config_manager(ctx, kafka_state)

    # Then
    java_opts = manager.java_opts[0]
    assert f"-Djavax.net.ssl.trustStore={manager.workload.paths.java_truststore}" in java_opts
    assert "-Djavax.net.ssl.trustStoreType=JKS" in java_opts


@pytest.mark.skipif(SUBSTRATE == "vm", reason="the JVM truststore is passed via JAVA_OPTS on VM")
def test_java_opts_are_heap_only_on_k8s(ctx: Context, kafka_state: State) -> None:
    """Checks the K8s charm keeps JAVA_OPTS to heap settings, the layer carries the rest."""
    # Given / When
    java_opts = config_manager(ctx, kafka_state).java_opts

    # Then
    assert java_opts == ["JAVA_OPTS='-Xms1G -Xmx1G -XX:+UseG1GC'"]


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="the UI serves TLS directly only on VM")
def test_server_config_enables_tls_bundle_on_vm(ctx: Context, kafka_state: State) -> None:
    """Checks the machine charm serves HTTPS through the Spring `server` ssl bundle."""
    # Given / When
    manager = config_manager(ctx, kafka_state)

    # Then
    assert manager.server_config == {"server": {"ssl": {"bundle": "server"}}}
    assert manager.spring_ssl_config["ssl"]["bundle"]["jks"]["server"]["keystore"]["type"] == (
        "PKCS12"
    )
    assert manager.context_path_config == {}


@pytest.mark.skipif(SUBSTRATE == "vm", reason="the context path only applies behind ingress")
def test_server_config_sets_context_path_on_k8s(ctx: Context, kafka_state: State) -> None:
    """Checks the K8s charm serves under a model/app scoped context path, without TLS."""
    # Given / When
    manager = config_manager(ctx, kafka_state)

    # Then
    assert manager.server_tls_config == {}
    assert manager.spring_ssl_config == {}
    assert manager.server_config["server"]["servlet"]["context-path"] == (
        manager.context.context_path
    )
