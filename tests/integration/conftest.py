#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import os
import pathlib
import subprocess
import typing

import jubilant
import pytest


def pytest_addoption(parser):
    """Define pytest parsers."""
    parser.addoption(
        "--tls",
        action="store_true",
        help="Whether to use TLS on tests or not",
    )


@pytest.fixture(scope="session", autouse=True)
def _pin_lxd_controller(request: pytest.FixtureRequest) -> None:
    """Pin `ops_test` (pytest-operator) to the LXD controller.

    After `concierge prepare` bootstraps both `concierge-lxd` and `concierge-k8s`,
    `jujudata.current_controller()` is not reliably the LXD one, so resolve the
    controller whose cloud is `localhost` and set it before `ops_test` builds its model.
    """
    controllers = json.loads(
        subprocess.run(
            ["juju", "controllers", "--format", "json"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    ).get("controllers", {})

    for name, info in controllers.items():
        if info.get("cloud") == "localhost":
            request.config.option.controller = name
            break


@pytest.fixture
def ui_charm() -> pathlib.Path:
    if not os.environ.get("CI"):
        # Build locally
        if os.system("charmcraft pack -v"):
            raise RuntimeError("Charm build failed")

    charm_path = pathlib.Path(os.getcwd())
    architecture = subprocess.run(
        ["dpkg", "--print-architecture"],
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout.strip()
    assert architecture in ("amd64", "arm64")
    packed_charms = list(charm_path.glob(f"*{architecture}.charm"))
    if len(packed_charms) == 1:
        # juju deploy, and juju bundle files expect local charms
        # to begin with `./` or `/` to distinguish them from Charmhub charms.
        # Therefore, we need to return an absolute path—a relative `pathlib.Path` does not start
        # with `./` when cast to a str.
        return packed_charms[0].resolve(strict=True)
    elif len(packed_charms) > 1:
        raise ValueError(
            f"More than one matching .charm file found at {charm_path=} "
            f"for {architecture=}: {packed_charms}."
        )
    else:
        raise ValueError(f"Unable to find .charm file for {architecture=} at {charm_path=}")


@pytest.fixture
def tls_enabled(request: pytest.FixtureRequest) -> bool:
    return typing.cast(bool, request.config.getoption("--tls"))


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    model = request.config.getoption("--model")
    keep_models = typing.cast(bool, request.config.getoption("--keep-models"))

    if model is None:
        with jubilant.temp_model(keep=keep_models) as juju:
            juju.wait_timeout = 10 * 60
            juju.model_config({"update-status-hook-interval": "90s"})
            yield juju

            log = juju.debug_log(limit=1000)
    else:
        juju = jubilant.Juju(model=model)
        yield juju
        log = juju.debug_log(limit=1000)

    if request.session.testsfailed:
        print(log, end="")
