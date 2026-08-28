#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import logging
from typing import cast
from unittest.mock import patch

import pytest
from ops.testing import Context, PeerRelation, Relation, State

from core.models import AppContext
from literals import KAFKA_REL, PEER_REL, SUBSTRATE, Status

from .helpers import KafkaUiCharm

logger = logging.getLogger(__name__)


def test_config_changed_defers_without_peer_relation(ctx: Context, base_state: State) -> None:
    """Checks `config-changed` defers when the peer relation is not yet available."""
    # Given
    state_in = dataclasses.replace(base_state, relations=[])

    # When
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Then
    assert len(state_out.deferred) == 1
    assert state_out.unit_status == Status.MISSING_KAFKA.value.status


def test_config_changed_defers_when_passwords_not_created_yet(
    ctx: Context, base_state: State
) -> None:
    """Checks a follower defers until the leader has written the app passwords."""
    # Given
    state_in = dataclasses.replace(base_state, leader=False)

    # When
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Then
    assert len(state_out.deferred) == 1


@pytest.mark.parametrize("is_leader", [True, False])
def test_init_app_passwords_is_leader_only(
    ctx: Context, base_state: State, peer_rel: PeerRelation, is_leader: bool
) -> None:
    """Checks the app-wide passwords are generated once, by the leader only."""
    # Given
    state_in = dataclasses.replace(base_state, leader=is_leader)

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        state_out = manager.run()

    # Then
    if is_leader:
        assert charm.context.app.admin_password
        assert charm.context.app.oauth_truststore_password
        secret_contents = {
            key: value
            for secret in state_out.secrets
            for key, value in secret.latest_content.items()
        }
        assert secret_contents.get(AppContext.ADMIN_PASSWORD)
        assert secret_contents.get(AppContext.OAUTH_TRUSTSTORE_PASSWORD)
    else:
        assert not charm.context.app.admin_password
        assert not charm.context.app.oauth_truststore_password


def test_init_app_passwords_is_idempotent(ctx: Context, base_state: State) -> None:
    """Checks existing app passwords are never regenerated."""
    # Given
    state_in = base_state

    # When
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    first_pass = {
        key: value for secret in state_out.secrets for key, value in secret.latest_content.items()
    }
    state_out = ctx.run(ctx.on.config_changed(), state_out)
    second_pass = {
        key: value for secret in state_out.secrets for key, value in secret.latest_content.items()
    }

    # Then
    assert first_pass[AppContext.ADMIN_PASSWORD] == second_pass[AppContext.ADMIN_PASSWORD]
    assert (
        first_pass[AppContext.OAUTH_TRUSTSTORE_PASSWORD]
        == second_pass[AppContext.OAUTH_TRUSTSTORE_PASSWORD]
    )


def test_start_blocks_without_kafka_relation(ctx: Context, base_state: State) -> None:
    """Checks the unit blocks when it has no `kafka-client` relation."""
    # Given
    state_in = base_state

    # When
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Then
    assert state_out.unit_status == Status.MISSING_KAFKA.value.status


def test_kafka_client_relation_created_waits_for_credentials(
    ctx: Context, base_state: State
) -> None:
    """Checks the context reports missing credentials when related but the databag is empty.

    NOTE: `health_check` hard-codes `Status.MISSING_KAFKA` rather than deferring to
    `context.kafka_client.status`, so `NO_KAFKA_CREDENTIALS` never reaches the unit
    status today. The context-level assertion below is the behaviour worth locking
    in; the unit-status assertion documents the current collapse of the two cases.
    """
    # Given
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data={})
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])

    # When
    with ctx(ctx.on.relation_created(kafka_rel), state_in) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        kafka_client_status = charm.context.kafka_client.status
        state_out = manager.run()

    # Then
    assert kafka_client_status == Status.NO_KAFKA_CREDENTIALS
    assert not charm.context.kafka_client.ready
    assert state_out.unit_status == Status.MISSING_KAFKA.value.status


def test_kafka_client_relation_changed_becomes_active(
    ctx: Context, base_state: State, kafka_client_data: dict[str, str]
) -> None:
    """Checks credentials arriving on `kafka-client` bring the unit to Active."""
    # Given
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data=kafka_client_data)
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])

    # When
    with patch("workload.Workload.restart") as patched_restart:
        state_out = ctx.run(ctx.on.relation_changed(kafka_rel), state_in)

    # Then
    patched_restart.assert_called_once()
    assert state_out.unit_status == Status.ACTIVE.value.status


def test_kafka_client_relation_changed_writes_config(
    ctx: Context, base_state: State, kafka_client_data: dict[str, str]
) -> None:
    """Checks the rendered application config is written to the workload."""
    # Given
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data=kafka_client_data)
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])

    # When
    with (
        ctx(ctx.on.relation_changed(kafka_rel), state_in) as manager,
        patch("workload.Workload.write") as patched_write,
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        manager.run()

    # Then
    written = {
        call.kwargs["path"]: call.kwargs["content"] for call in patched_write.call_args_list
    }
    application_config = charm.workload.paths.application_local_config
    assert application_config in written
    assert kafka_client_data["endpoints"] in written[application_config]


def test_kafka_client_relation_broken_blocks(
    ctx: Context, base_state: State, kafka_client_data: dict[str, str]
) -> None:
    """Checks losing the `kafka-client` relation puts the unit back to Blocked."""
    # Given
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data=kafka_client_data)
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])

    # When
    state_out = ctx.run(ctx.on.relation_broken(kafka_rel), state_in)

    # Then
    assert state_out.unit_status == Status.MISSING_KAFKA.value.status


def test_unchanged_config_does_not_restart(
    ctx: Context, base_state: State, kafka_client_data: dict[str, str]
) -> None:
    """Checks a no-op `config-changed` neither rewrites config nor restarts the service."""
    # Given
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data=kafka_client_data)
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])

    # When -- first pass renders and stores the config the workload would now hold
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        state_out = manager.run()
        rendered = charm.config_manager.clean_yaml_config

    # ... second pass sees that same config already on disk
    with (
        patch("workload.Workload.read", return_value=rendered.split("\n")),
        patch("workload.Workload.restart") as patched_restart,
        patch("workload.Workload.write") as patched_write,
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_out)

    # Then
    patched_restart.assert_not_called()
    assert not patched_write.call_args_list
    assert state_out.unit_status == Status.ACTIVE.value.status


def test_update_status_emits_config_changed(ctx: Context, base_state: State) -> None:
    """Checks `update-status` re-runs the reconciler."""
    # Given
    state_in = base_state

    # When
    ctx.run(ctx.on.update_status(), state_in)

    # Then
    assert any(type(event).__name__ == "ConfigChangedEvent" for event in ctx.emitted_events)


def test_upgrade_charm_reconciles(ctx: Context, base_state: State) -> None:
    """Checks `upgrade-charm` re-runs the reconciler."""
    # Given
    state_in = base_state

    # When
    state_out = ctx.run(ctx.on.upgrade_charm(), state_in)

    # Then
    assert any(type(event).__name__ == "ConfigChangedEvent" for event in ctx.emitted_events)
    assert state_out.unit_status == Status.MISSING_KAFKA.value.status


def test_installing_status_when_workload_not_installed(ctx: Context, base_state: State) -> None:
    """Checks the unit reports Maintenance while the workload is not installed yet."""
    # Given
    state_in = base_state

    # When
    with patch("workload.Workload.installed", False):
        state_out = ctx.run(ctx.on.update_status(), state_in)

    # Then
    assert state_out.unit_status == Status.INSTALLING.value.status


def test_service_not_running_status(
    ctx: Context, base_state: State, kafka_client_data: dict[str, str]
) -> None:
    """Checks the unit blocks when the workload service is down."""
    # Given
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data=kafka_client_data)
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])

    # When
    with patch("workload.Workload.active", return_value=False):
        state_out = ctx.run(ctx.on.update_status(), state_in)

    # Then
    assert state_out.unit_status == Status.SERVICE_NOT_RUNNING.value.status


def test_service_unhealthy_status(
    ctx: Context,
    base_state: State,
    kafka_client_data: dict[str, str],
    patched_health_response,
) -> None:
    """Checks the unit blocks when the web server does not answer with 200."""
    # Given
    patched_health_response.return_value.status_code = 503
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data=kafka_client_data)
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])

    # When
    state_out = ctx.run(ctx.on.update_status(), state_in)

    # Then
    assert state_out.unit_status == Status.SERVICE_UNHEALTHY.value.status


def test_peer_relation_carries_app_passwords(ctx: Context, base_state: State) -> None:
    """Checks app passwords land in the peer relation's Juju secrets, not plain databag."""
    # Given
    state_in = base_state

    # When
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Then
    peer_out = state_out.get_relation(
        next(rel.id for rel in state_out.relations if rel.endpoint == PEER_REL)
    )
    assert AppContext.ADMIN_PASSWORD not in peer_out.local_app_data
    secret_contents = {
        key: value for secret in state_out.secrets for key, value in secret.latest_content.items()
    }
    assert secret_contents.get(AppContext.ADMIN_PASSWORD)


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="snap install is VM-only")
def test_install_blocks_on_snap_failure(ctx: Context, base_state: State) -> None:
    """Checks the unit blocks when the snap cannot be installed."""
    # Given
    state_in = base_state

    # When
    with patch("workload.Workload.install", return_value=False):
        state_out = ctx.run(ctx.on.install(), state_in)

    # Then
    assert state_out.unit_status == Status.SNAP_NOT_INSTALLED.value.status


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="snap install is VM-only")
def test_upgrade_charm_blocks_on_snap_failure(ctx: Context, base_state: State) -> None:
    """Checks `upgrade-charm` blocks when the snap cannot be re-installed."""
    # Given
    state_in = base_state

    # When
    with patch("workload.Workload.install", return_value=False):
        state_out = ctx.run(ctx.on.upgrade_charm(), state_in)

    # Then
    assert state_out.unit_status == Status.SNAP_NOT_INSTALLED.value.status


@pytest.mark.skipif(SUBSTRATE == "vm", reason="ingress is K8s-only")
def test_missing_ingress_blocks(
    ctx: Context, base_state: State, kafka_client_data: dict[str, str], patched_ingress
) -> None:
    """Checks the unit blocks while no ingress relation is ready."""
    # Given
    patched_ingress.return_value = False
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data=kafka_client_data)
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])

    # When
    state_out = ctx.run(ctx.on.update_status(), state_in)

    # Then
    assert state_out.unit_status == Status.MISSING_INGRESS.value.status


@pytest.mark.skipif(SUBSTRATE == "vm", reason="pebble-ready is K8s-only")
def test_pebble_ready_reconciles(
    ctx: Context, base_state: State, kafka_client_data: dict[str, str]
) -> None:
    """Checks `pebble-ready` runs the reconciler and starts the service."""
    # Given
    kafka_rel = Relation(KAFKA_REL, KAFKA_REL, remote_app_data=kafka_client_data)
    state_in = dataclasses.replace(base_state, relations=[*base_state.relations, kafka_rel])
    container = next(iter(state_in.containers))

    # When
    with patch("workload.Workload.restart") as patched_restart:
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

    # Then
    patched_restart.assert_called_once()
    assert state_out.unit_status == Status.ACTIVE.value.status
