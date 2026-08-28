#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from typing import cast
from unittest.mock import MagicMock

import pytest
from ops.testing import Context, State

from core.workload import WorkloadBase
from literals import CONFIG_DIR, GROUP, SERVICE_NAME, SUBSTRATE, USER_NAME

from .helpers import KafkaUiCharm

if SUBSTRATE == "vm":
    from charms.operator_libs_linux.v2 import snap

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def patched_workload():
    """Override the suite-wide workload stub -- these tests exercise the real methods."""
    yield


@pytest.fixture()
def workload(ctx: Context, base_state: State, patched_snap_cache) -> WorkloadBase:
    """Return the charm's real workload, with only the substrate primitives mocked out.

    Building it needs a live charm, and `install` is the one hook that reaches the
    workload without running the whole reconciler. The snap mock is reset afterwards
    so tests see only their own calls.
    """
    with ctx(ctx.on.install(), base_state) as manager:
        charm = cast(KafkaUiCharm, manager.charm)
        instance = charm.workload
        manager.run()

    if patched_snap_cache is not None:
        patched_snap_cache.reset_mock()

    return instance


def test_generate_password_is_long_and_random(workload: WorkloadBase) -> None:
    """Checks generated passwords are 32 alphanumeric characters and never repeat."""
    # Given / When
    passwords = {workload.generate_password() for _ in range(10)}

    # Then
    assert len(passwords) == 10
    for password in passwords:
        assert len(password) == 32
        assert password.isalnum()


def test_generate_password_respects_length(workload: WorkloadBase) -> None:
    """Checks the requested password length is honoured."""
    # Given / When / Then
    assert len(workload.generate_password(length=64)) == 64


@pytest.mark.parametrize(
    "raw,expected",
    [
        (["A=1", "B=2"], {"A": "1", "B": "2"}),
        # values may legitimately contain '=' -- only the first one splits
        (["JAVA_OPTS=-Da=b -Dc=d"], {"JAVA_OPTS": "-Da=b -Dc=d"}),
        # an empty value is valid, an empty key is not
        (["EMPTY=", "=orphan"], {"EMPTY": ""}),
        ([""], {}),
    ],
)
def test_map_env_parses_environment_files(
    workload: WorkloadBase, raw: list[str], expected: dict[str, str]
) -> None:
    """Checks `/etc/environment` lines are parsed into a mapping."""
    # Given / When / Then
    assert workload.map_env(raw) == expected


def test_set_environment_merges_with_existing(workload: WorkloadBase) -> None:
    """Checks new variables are merged into the existing environment, not replacing it."""
    # Given
    workload.read = MagicMock(return_value=["KEEP=me", "JAVA_OPTS=old"])
    workload.write = MagicMock()

    # When
    workload.set_environment(["JAVA_OPTS=new"])

    # Then
    written = workload.write.call_args.kwargs["content"]
    assert workload.write.call_args.kwargs["path"] == workload.paths.env
    assert workload.map_env(written.splitlines()) == {"KEEP": "me", "JAVA_OPTS": "new"}


def test_paths_live_under_the_config_dir(workload: WorkloadBase) -> None:
    """Checks the config, store and truststore paths all derive from the config directory."""
    # Given / When
    paths = workload.paths

    # Then
    assert paths.config_dir == CONFIG_DIR
    assert paths.application_local_config == f"{CONFIG_DIR}/application-local.yml"
    assert paths.keystore == f"{CONFIG_DIR}/keystore.p12"
    assert paths.truststore == f"{CONFIG_DIR}/truststore.jks"
    # the writable copy must not be the read-only JDK one
    assert paths.java_truststore == f"{CONFIG_DIR}/cacerts"
    assert paths.java_truststore != paths.java_cacerts


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="the snap only exists on the machine charm")
def test_install_holds_the_snap(workload: WorkloadBase, patched_snap_cache: MagicMock) -> None:
    """Checks the snap is pinned to a revision and held against automatic refreshes."""
    # Given / When
    installed = workload.install()

    # Then
    assert installed
    patched_snap_cache.ensure.assert_called_once()
    assert patched_snap_cache.ensure.call_args.kwargs["revision"]
    patched_snap_cache.hold.assert_called_once()


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="the snap only exists on the machine charm")
def test_install_returns_false_on_snap_error(
    workload: WorkloadBase, patched_snap_cache: MagicMock
) -> None:
    """Checks a snap failure is reported rather than raised, so the charm can block."""
    # Given
    patched_snap_cache.ensure.side_effect = snap.SnapError("no such snap")

    # When / Then
    assert not workload.install()


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="the snap only exists on the machine charm")
def test_installed_is_false_without_the_service(
    workload: WorkloadBase, patched_snap_cache: MagicMock
) -> None:
    """Checks a snap without the daemon service is not considered installed."""
    # Given
    patched_snap_cache.services = {}

    # When / Then
    assert not workload.installed


@pytest.mark.skipif(SUBSTRATE == "vm", reason="Pebble layers only exist on the K8s charm")
def test_layer_starts_the_ui_service(workload: WorkloadBase) -> None:
    """Checks the Pebble layer launches the UI jar as the expected user."""
    # Given / When
    service = workload.layer.services[SERVICE_NAME]

    # Then
    assert service.startup == "enabled"
    assert service.user == USER_NAME
    assert service.group == GROUP
    assert service.command.startswith("java ")
    assert workload.paths.jar in service.command
    assert f"-Djavax.net.ssl.trustStore={workload.paths.java_truststore}" in service.command
    assert "JAVA_OPTS" in service.environment


@pytest.mark.skipif(SUBSTRATE == "vm", reason="container connectivity only applies on K8s")
def test_container_can_connect_follows_pebble(workload: WorkloadBase) -> None:
    """Checks the charm's readiness gate tracks the Pebble socket."""
    # Given / When / Then
    assert workload.container_can_connect
    assert workload.installed
