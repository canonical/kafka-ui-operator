#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import logging
from typing import cast

import pytest
from ops.testing import Context, PeerRelation, Secret, State

from core.models import AppContext
from literals import PEER_REL

from .helpers import KafkaUiCharm, stored_peer_data

logger = logging.getLogger(__name__)

AUTH_CONFIG_KEY = "system-users"


@pytest.fixture()
def peer_rel_with_admin() -> PeerRelation:
    """Build a peer relation whose admin password has already been generated."""
    return PeerRelation(
        PEER_REL, PEER_REL, local_app_data={AppContext.ADMIN_PASSWORD: "old-password"}
    )


@pytest.mark.parametrize("secret_provided", [True, False])
def test_set_admin_password_from_user_secret(
    ctx: Context, base_state: State, peer_rel_with_admin: PeerRelation, secret_provided: bool
) -> None:
    """Checks a user-provided secret rotates the admin password only when configured."""
    # Given
    auth_secret = Secret(label="auth-secret", tracked_content={"admin": "new-password"})
    state_in = dataclasses.replace(
        base_state,
        relations=[peer_rel_with_admin],
        secrets=[auth_secret],
        config={AUTH_CONFIG_KEY: auth_secret.id} if secret_provided else {},
    )

    # When
    with ctx(ctx.on.secret_changed(auth_secret), state_in) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        previous_password = charm.context.app.admin_password
        state_out = manager.run()

    # Then
    assert previous_password == "old-password"

    stored = stored_peer_data(state_out, peer_rel_with_admin.id)
    if secret_provided:
        assert stored.get(AppContext.ADMIN_PASSWORD) == "new-password"
    else:
        assert stored.get(AppContext.ADMIN_PASSWORD) == "old-password"


def test_admin_password_change_triggers_reconcile(
    ctx: Context, base_state: State, peer_rel_with_admin: PeerRelation
) -> None:
    """Checks rotating the admin password re-renders and restarts the workload."""
    # Given
    auth_secret = Secret(label="auth-secret", tracked_content={"admin": "new-password"})
    state_in = dataclasses.replace(
        base_state,
        relations=[peer_rel_with_admin],
        secrets=[auth_secret],
        config={AUTH_CONFIG_KEY: auth_secret.id},
    )

    # When
    ctx.run(ctx.on.secret_changed(auth_secret), state_in)

    # Then
    assert any(type(event).__name__ == "ConfigChangedEvent" for event in ctx.emitted_events)


def test_unchanged_password_is_a_noop(
    ctx: Context, base_state: State, peer_rel_with_admin: PeerRelation
) -> None:
    """Checks a secret holding the current password does not rotate anything."""
    # Given
    auth_secret = Secret(label="auth-secret", tracked_content={"admin": "old-password"})
    state_in = dataclasses.replace(
        base_state,
        relations=[peer_rel_with_admin],
        secrets=[auth_secret],
        config={AUTH_CONFIG_KEY: auth_secret.id},
    )

    # When
    state_out = ctx.run(ctx.on.secret_changed(auth_secret), state_in)

    # Then
    assert (
        stored_peer_data(state_out, peer_rel_with_admin.id).get(AppContext.ADMIN_PASSWORD)
        == "old-password"
    )


def test_secret_changed_is_leader_only(
    ctx: Context, base_state: State, peer_rel_with_admin: PeerRelation
) -> None:
    """Checks a follower never writes the shared admin password."""
    # Given
    auth_secret = Secret(label="auth-secret", tracked_content={"admin": "new-password"})
    state_in = dataclasses.replace(
        base_state,
        relations=[peer_rel_with_admin],
        secrets=[auth_secret],
        config={AUTH_CONFIG_KEY: auth_secret.id},
        leader=False,
    )

    # When
    state_out = ctx.run(ctx.on.secret_changed(auth_secret), state_in)

    # Then
    assert (
        stored_peer_data(state_out, peer_rel_with_admin.id).get(AppContext.ADMIN_PASSWORD)
        == "old-password"
    )


def test_load_auth_secret_rejects_unmanaged_users(
    ctx: Context, base_state: State, peer_rel_with_admin: PeerRelation
) -> None:
    """Checks only the internally managed `admin` user can be set through the secret."""
    # Given
    auth_secret = Secret(
        label="auth-secret",
        tracked_content={"admin": "new-password", "someone-else": "nope"},
    )
    state_in = dataclasses.replace(
        base_state,
        relations=[peer_rel_with_admin],
        secrets=[auth_secret],
        config={AUTH_CONFIG_KEY: auth_secret.id},
    )

    # When
    with ctx(ctx.on.secret_changed(auth_secret), state_in) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        credentials = charm.user_secrets.load_auth_secret()
        manager.run()

    # Then
    assert credentials == {"admin": "new-password"}


def test_load_auth_secret_without_config_returns_nothing(
    ctx: Context, base_state: State, peer_rel_with_admin: PeerRelation
) -> None:
    """Checks no secret is read while `system-users` is unset."""
    # Given
    state_in = dataclasses.replace(base_state, relations=[peer_rel_with_admin])

    # When
    with ctx(ctx.on.install(), state_in) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        credentials = charm.user_secrets.load_auth_secret()
        manager.run()

    # Then
    assert credentials == {}


def test_load_auth_secret_survives_a_missing_secret(
    ctx: Context, base_state: State, peer_rel_with_admin: PeerRelation
) -> None:
    """Checks a dangling secret ID is logged rather than crashing the charm."""
    # Given
    state_in = dataclasses.replace(
        base_state,
        relations=[peer_rel_with_admin],
        config={AUTH_CONFIG_KEY: "secret:cvh7kruupa1s46bqvuig"},
    )

    # When
    with ctx(ctx.on.install(), state_in) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        credentials = charm.user_secrets.load_auth_secret()
        manager.run()

    # Then
    assert credentials == {}
