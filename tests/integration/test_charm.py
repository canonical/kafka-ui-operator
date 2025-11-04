#!/usr/bin/env python3
# Copyright 2025 marc
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import jubilant
import pytest
import requests
from helpers import (
    APP_NAME,
    CONNECT_APP,
    CONNECT_CHANNEL,
    KAFKA_APP,
    KAFKA_CHANNEL,
    KARAPACE_APP,
    KARAPACE_CHANNEL,
    PORT,
    PROTO,
    SECRET_KEY,
    TLS_APP,
    all_active_idle,
    get_secret_by_label,
    get_unit_ipv4_address,
    set_password,
)

logger = logging.getLogger(__name__)


@pytest.fixture
def apps() -> list[str]:
    """Return list of Kafka ecosystem apps."""
    return [APP_NAME, CONNECT_APP, KAFKA_APP, KARAPACE_APP]


def test_build_and_deploy(juju: jubilant.Juju, ui_charm: Path, tls_enabled: bool, apps: list[str]):
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    juju.deploy(
        KAFKA_APP,
        app=KAFKA_APP,
        trust=True,
        channel=KAFKA_CHANNEL,
        config={"roles": "broker,controller"},
    )
    juju.deploy(CONNECT_APP, app=CONNECT_APP, trust=True, channel=CONNECT_CHANNEL)
    juju.deploy(KARAPACE_APP, app=KARAPACE_APP, trust=True, channel=KARAPACE_CHANNEL)
    juju.deploy(ui_charm, app=APP_NAME, trust=True)

    if tls_enabled:
        juju.deploy(TLS_APP, app=TLS_APP, trust=True)

    _apps = apps + ([] if not tls_enabled else [TLS_APP])
    juju.wait(
        lambda status: jubilant.all_agents_idle(status, *_apps)
        and jubilant.all_blocked(status, APP_NAME, CONNECT_APP, KARAPACE_APP)
        and jubilant.all_active(status, KAFKA_APP),
        delay=3,
        timeout=1200,
        successes=10,
    )

    status = juju.status()
    assert status.apps[APP_NAME].app_status.current == "blocked"
    assert status.apps[CONNECT_APP].app_status.current == "blocked"
    assert status.apps[KARAPACE_APP].app_status.current == "blocked"
    assert status.apps[KAFKA_APP].app_status.current == "active"


def test_integrate(juju: jubilant.Juju, tls_enabled: bool, apps: list[str]):
    # First integrate non-UI apps with Kafka
    juju.integrate(CONNECT_APP, KAFKA_APP)
    juju.integrate(KARAPACE_APP, KAFKA_APP)

    juju.wait(
        lambda status: all_active_idle(status, CONNECT_APP, KAFKA_APP, KARAPACE_APP),
        delay=3,
        timeout=900,
        successes=10,
    )

    # Integrate TLS
    if tls_enabled:
        for app in apps:
            juju.integrate(TLS_APP, f"{app}:certificates")

    juju.wait(
        lambda status: all_active_idle(status, CONNECT_APP, KAFKA_APP, KARAPACE_APP),
        delay=3,
        timeout=900,
        successes=10,
    )

    # Now integrate all apps with UI app.
    juju.integrate(APP_NAME, KAFKA_APP)
    juju.integrate(APP_NAME, CONNECT_APP)
    juju.integrate(APP_NAME, KARAPACE_APP)

    juju.wait(
        lambda status: all_active_idle(status, *apps),
        delay=3,
        timeout=900,
        successes=10,
    )

    status = juju.status()
    for app in apps:
        assert status.apps[app].app_status.current == "active"


def test_ui(juju: jubilant.Juju, tls_enabled: bool, tmp_path):
    secret_data = get_secret_by_label(juju.model, f"cluster.{APP_NAME}.app", owner=APP_NAME)
    password = secret_data.get(SECRET_KEY)

    if not password:
        raise Exception("Can't fetch the admin user's password.")

    # Verify that we're using TLS provider's cert in case TLS enabled.
    _verify = False
    if tls_enabled:
        result = juju.run(f"{TLS_APP}/0", "get-ca-certificate")
        ca = result.results.get("ca-certificate")
        ca_file = f"{tmp_path}/ca.pem"
        logger.info(f"Saving CA cert to {ca_file} for requests verification.")
        open(ca_file, "w").write(ca)
        _verify = ca_file

    unit_ip = get_unit_ipv4_address(juju.model, f"{APP_NAME}/0")
    url = f"{PROTO}://{unit_ip}:{PORT}"

    login_resp = requests.post(
        f"{url}/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"username": "admin", "password": password},
        verify=_verify,
    )
    assert login_resp.status_code == 200
    # Successful login would lead to a redirect
    assert len(login_resp.history) > 0

    cookies = login_resp.history[0].cookies
    clusters_resp = requests.get(
        f"{url}/api/clusters",
        headers={"Content-Type": "application/json"},
        cookies=cookies,
        verify=_verify,
    )

    clusters_json = clusters_resp.json()
    logger.info(f"{clusters_json=}")
    assert len(clusters_json) > 0
    assert clusters_json[0].get("status") == "online"


def test_password_rotation(juju: jubilant.Juju, apps: list[str]):
    secret_data = get_secret_by_label(juju.model, f"cluster.{APP_NAME}.app", owner=APP_NAME)
    old_password = secret_data.get(SECRET_KEY)

    new_password = "newStrongPa$$"
    set_password(juju, password=new_password)
    assert new_password != old_password

    juju.wait(
        lambda status: all_active_idle(status, *apps),
        delay=3,
        timeout=900,
        successes=10,
    )

    # Check we can login with the new password
    unit_ip = get_unit_ipv4_address(juju.model, f"{APP_NAME}/0")
    url = f"{PROTO}://{unit_ip}:{PORT}"
    login_resp = requests.post(
        f"{url}/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"username": "admin", "password": new_password},
        verify=False,
    )

    assert login_resp.status_code == 200
    # Successful login would lead to a redirect
    assert len(login_resp.history) > 0
