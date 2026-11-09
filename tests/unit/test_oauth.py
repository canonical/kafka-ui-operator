#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import logging
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from ops.testing import Context, PeerRelation, Relation, State

from core.models import AppContext
from literals import (
    JAVA_CACERTS_DEFAULT_PASSWORD,
    OAUTH_CA_ALIAS_PREFIX,
    OAUTH_CA_REL,
    OAUTH_REL,
    PEER_REL,
    SUBSTRATE,
)

from .helpers import KafkaUiCharm, TLSArtifacts, stored_peer_data

logger = logging.getLogger(__name__)

CLIENT_SECRET = "s3cr3t-issued-by-hydra"

OAUTH_REQUIRER = "charmlibs.interfaces.oauth.OAuthRequirer"
CERT_TRANSFER_REQUIRER = "charmlibs.interfaces.certificate_transfer.CertificateTransferRequires"


@pytest.fixture()
def provider_info():
    """Build the provider config `OAuthRequirer.get_provider_info` would return."""
    info = MagicMock()
    info.client_secret = CLIENT_SECRET
    return info


@pytest.fixture()
def java_truststore_exists(monkeypatch: pytest.MonkeyPatch):
    """Control whether the writable JVM truststore already exists on the workload."""

    def _set(exists: bool) -> None:
        target = "LocalPath" if SUBSTRATE == "vm" else "ContainerPath"
        monkeypatch.setattr(f"charmlibs.pathops.{target}.exists", lambda _: exists)

    return _set


@pytest.fixture()
def peer_rel_with_passwords() -> PeerRelation:
    """Build a peer relation the leader has already initialised."""
    return PeerRelation(
        PEER_REL,
        PEER_REL,
        local_app_data={
            AppContext.ADMIN_PASSWORD: "admin-pass",
            AppContext.OAUTH_TRUSTSTORE_PASSWORD: "truststore-pass",
        },
    )


@pytest.mark.parametrize("is_leader", [True, False])
def test_oauth_relation_changed_stores_client_secret(
    ctx: Context,
    base_state: State,
    peer_rel: PeerRelation,
    oauth_data: dict[str, str],
    provider_info: MagicMock,
    is_leader: bool,
) -> None:
    """Checks the client secret issued by the provider is stored, by the leader only."""
    # Given
    oauth_rel = Relation(OAUTH_REL, OAUTH_REL, remote_app_data=oauth_data)
    state_in = dataclasses.replace(base_state, relations=[peer_rel, oauth_rel], leader=is_leader)

    # When
    with patch(f"{OAUTH_REQUIRER}.get_provider_info", return_value=provider_info):
        state_out = ctx.run(ctx.on.relation_changed(oauth_rel), state_in)

    # Then
    stored = stored_peer_data(state_out, peer_rel.id)
    if is_leader:
        assert stored.get(AppContext.OAUTH_CLIENT_SECRET) == CLIENT_SECRET
    else:
        assert not stored.get(AppContext.OAUTH_CLIENT_SECRET)


def test_oauth_relation_changed_defers_without_client_secret(
    ctx: Context, base_state: State, peer_rel: PeerRelation, oauth_data: dict[str, str]
) -> None:
    """Checks the handler waits until the provider has actually issued a secret."""
    # Given
    oauth_rel = Relation(OAUTH_REL, OAUTH_REL, remote_app_data=oauth_data)
    state_in = dataclasses.replace(base_state, relations=[peer_rel, oauth_rel])

    # When
    with patch(f"{OAUTH_REQUIRER}.get_provider_info", return_value=None):
        state_out = ctx.run(ctx.on.relation_changed(oauth_rel), state_in)

    # Then
    assert [event.name for event in state_out.deferred].count("oauth_relation_changed") == 1
    assert not stored_peer_data(state_out, peer_rel.id).get(AppContext.OAUTH_CLIENT_SECRET)


@pytest.mark.parametrize("is_leader", [True, False])
def test_oauth_relation_broken_clears_client_secret(
    ctx: Context, base_state: State, oauth_data: dict[str, str], is_leader: bool
) -> None:
    """Checks losing the provider clears the stored client secret, on the leader only."""
    # Given
    peer_rel = PeerRelation(
        PEER_REL, PEER_REL, local_app_data={AppContext.OAUTH_CLIENT_SECRET: CLIENT_SECRET}
    )
    oauth_rel = Relation(OAUTH_REL, OAUTH_REL, remote_app_data=oauth_data)
    state_in = dataclasses.replace(base_state, relations=[peer_rel, oauth_rel], leader=is_leader)

    # When
    state_out = ctx.run(ctx.on.relation_broken(oauth_rel), state_in)

    # Then
    stored = stored_peer_data(state_out, peer_rel.id)
    if is_leader:
        assert not stored.get(AppContext.OAUTH_CLIENT_SECRET)
    else:
        assert stored.get(AppContext.OAUTH_CLIENT_SECRET) == CLIENT_SECRET


def test_reconcile_ca_truststore_seeds_the_jvm_truststore(
    ctx: Context,
    base_state: State,
    peer_rel_with_passwords: PeerRelation,
    tls_artifacts: TLSArtifacts,
    java_truststore_exists,
) -> None:
    """Checks a missing truststore is seeded from the JDK cacerts and re-passworded."""
    # Given
    java_truststore_exists(False)
    state_in = dataclasses.replace(base_state, relations=[peer_rel_with_passwords])

    # When
    with (
        ctx(ctx.on.install(), state_in) as manager,
        patch(f"{CERT_TRANSFER_REQUIRER}.get_all_certificates", return_value={tls_artifacts.ca}),
        patch("managers.tls.TLSManager.set_truststore_password") as patched_set_password,
        patch("managers.tls.TLSManager.set_oauth_truststore", return_value=True),
        patch("workload.Workload.exec") as patched_exec,
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        changed = charm.oauth.reconcile_ca_truststore()
        paths = charm.workload.paths
        manager.run()

    # Then
    assert changed
    assert ["cp", paths.java_cacerts, paths.java_truststore] in [
        call.kwargs.get("command", call.args[0] if call.args else None)
        for call in patched_exec.call_args_list
    ]
    patched_set_password.assert_called_once_with(
        keystore=paths.java_truststore,
        old_password=JAVA_CACERTS_DEFAULT_PASSWORD,
        new_password="truststore-pass",
    )


def test_reconcile_ca_truststore_skips_seeding_when_present(
    ctx: Context,
    base_state: State,
    peer_rel_with_passwords: PeerRelation,
    tls_artifacts: TLSArtifacts,
    java_truststore_exists,
) -> None:
    """Checks an existing truststore is reused rather than re-seeded on every event."""
    # Given
    java_truststore_exists(True)
    state_in = dataclasses.replace(base_state, relations=[peer_rel_with_passwords])

    # When
    with (
        ctx(ctx.on.install(), state_in) as manager,
        patch(f"{CERT_TRANSFER_REQUIRER}.get_all_certificates", return_value={tls_artifacts.ca}),
        patch("managers.tls.TLSManager.set_truststore_password") as patched_set_password,
        patch("managers.tls.TLSManager.set_oauth_truststore", return_value=False) as patched_set,
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        changed = charm.oauth.reconcile_ca_truststore()
        manager.run()

    # Then
    patched_set_password.assert_not_called()
    patched_set.assert_called_once_with({tls_artifacts.ca})
    assert not changed


def test_oauth_ca_changed_restarts_only_when_truststore_changed(
    ctx: Context,
    base_state: State,
    peer_rel_with_passwords: PeerRelation,
    tls_artifacts: TLSArtifacts,
    java_truststore_exists,
) -> None:
    """Checks the workload is restarted only if the transferred CAs actually changed."""
    # Given
    java_truststore_exists(True)
    ca_rel = Relation(OAUTH_CA_REL, OAUTH_CA_REL)
    state_in = dataclasses.replace(base_state, relations=[peer_rel_with_passwords, ca_rel])

    for truststore_changed, expected_restarts in ((False, 0), (True, 1)):
        # When
        with (
            ctx(ctx.on.install(), state_in) as manager,
            patch(
                f"{CERT_TRANSFER_REQUIRER}.get_all_certificates",
                return_value={tls_artifacts.ca},
            ),
            patch(
                "events.oauth.OAuthHandler.reconcile_ca_truststore",
                return_value=truststore_changed,
            ),
            patch("workload.Workload.restart") as patched_restart,
        ):
            charm = cast(KafkaUiCharm, manager.charm)
            charm.oauth._on_oauth_ca_changed(MagicMock())
            manager.run()

        # Then
        assert patched_restart.call_count == expected_restarts


def test_oauth_ca_changed_defers_without_transferred_certificates(
    ctx: Context, base_state: State, peer_rel_with_passwords: PeerRelation
) -> None:
    """Checks the truststore reconcile waits until a CA has actually been transferred."""
    # Given
    ca_rel = Relation(OAUTH_CA_REL, OAUTH_CA_REL)
    state_in = dataclasses.replace(base_state, relations=[peer_rel_with_passwords, ca_rel])
    event = MagicMock()

    # When
    with (
        ctx(ctx.on.install(), state_in) as manager,
        patch(f"{CERT_TRANSFER_REQUIRER}.get_all_certificates", return_value=set()),
        patch("events.oauth.OAuthHandler.reconcile_ca_truststore") as patched_reconcile,
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        charm.oauth._on_oauth_ca_changed(event)
        manager.run()

    # Then
    event.defer.assert_called_once()
    patched_reconcile.assert_not_called()


def test_oauth_ca_removed_reconciles_without_certificates(
    ctx: Context, base_state: State, peer_rel_with_passwords: PeerRelation
) -> None:
    """Checks dropping the CA relation still reconciles, so stale CAs are removed."""
    # Given
    state_in = dataclasses.replace(base_state, relations=[peer_rel_with_passwords])
    event = MagicMock()

    # When
    with (
        ctx(ctx.on.install(), state_in) as manager,
        patch(
            "events.oauth.OAuthHandler.reconcile_ca_truststore", return_value=True
        ) as patched_reconcile,
        patch("workload.Workload.restart") as patched_restart,
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        charm.oauth._on_oauth_ca_removed(event)
        manager.run()

    # Then
    patched_reconcile.assert_called_once()
    patched_restart.assert_called_once()
    event.defer.assert_not_called()


def test_oauth_ca_reconcile_defers_before_passwords_exist(
    ctx: Context, base_state: State, peer_rel: PeerRelation, tls_artifacts: TLSArtifacts
) -> None:
    """Checks the truststore is not touched before the leader has set its password."""
    # Given -- an empty peer databag, so `java_truststore_password` is still unset
    state_in = dataclasses.replace(base_state, relations=[peer_rel], leader=False)
    event = MagicMock()

    # When
    with (
        ctx(ctx.on.install(), state_in) as manager,
        patch(f"{CERT_TRANSFER_REQUIRER}.get_all_certificates", return_value={tls_artifacts.ca}),
        patch("events.oauth.OAuthHandler.reconcile_ca_truststore") as patched_reconcile,
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        charm.oauth._on_oauth_ca_changed(event)
        manager.run()

    # Then
    event.defer.assert_called_once()
    patched_reconcile.assert_not_called()


def test_oauth_ca_alias_is_content_derived(
    ctx: Context, base_state: State, tls_artifacts: TLSArtifacts
) -> None:
    """Checks truststore aliases are stable for a CA and unique between CAs."""
    # Given
    with ctx(ctx.on.install(), base_state) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        alias = charm.tls_manager.oauth_ca_alias
        manager.run()

    # When / Then
    assert alias(tls_artifacts.ca) == alias(tls_artifacts.ca)
    assert alias(tls_artifacts.ca).startswith(OAUTH_CA_ALIAS_PREFIX)
    assert alias(tls_artifacts.ca) != alias(tls_artifacts.certificate)


def test_reconcile_client_config_reissues_redirect_uri(
    ctx: Context,
    base_state: State,
    peer_rel_with_passwords: PeerRelation,
    oauth_data: dict[str, str],
) -> None:
    """Checks the OAuth client config is re-issued once the unit address is known.

    On K8s the redirect URI is only correct once the ingress has published a URL,
    which is exactly why this reconcile exists on both substrates.
    """
    # Given
    oauth_rel = Relation(OAUTH_REL, OAUTH_REL, remote_app_data=oauth_data)
    state_in = dataclasses.replace(base_state, relations=[peer_rel_with_passwords, oauth_rel])

    # When
    with (
        ctx(ctx.on.install(), state_in) as manager,
        patch(f"{OAUTH_REQUIRER}.update_client_config") as patched_update,
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        charm.oauth.reconcile_client_config()
        expected_uri = f"{charm.context.ingress_url}/login/oauth2/code/iam"
        manager.run()

    # Then
    patched_update.assert_called()
    client_config = patched_update.call_args.args[0]
    assert client_config.redirect_uri == expected_uri
    assert client_config.grant_types == ["authorization_code"]


def test_reconcile_client_config_is_leader_only(
    ctx: Context,
    base_state: State,
    peer_rel_with_passwords: PeerRelation,
    oauth_data: dict[str, str],
) -> None:
    """Checks a follower never rewrites the shared OAuth client config."""
    # Given
    oauth_rel = Relation(OAUTH_REL, OAUTH_REL, remote_app_data=oauth_data)
    state_in = dataclasses.replace(
        base_state, relations=[peer_rel_with_passwords, oauth_rel], leader=False
    )

    # When
    with (
        ctx(ctx.on.install(), state_in) as manager,
        patch(f"{OAUTH_REQUIRER}.update_client_config") as patched_update,
    ):
        charm = cast(KafkaUiCharm, manager.charm)
        charm.oauth.reconcile_client_config()
        manager.run()

    # Then
    patched_update.assert_not_called()
