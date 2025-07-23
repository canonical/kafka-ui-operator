#!/usr/bin/env python3
# Copyright 2025 marc
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import jubilant
import requests
from helpers import (
    APP_NAME,
    CONNECT_APP,
    CONNECT_CHANNEL,
    KAFKA_APP,
    KAFKA_CHANNEL,
    PORT,
    PROTO,
    SECRET_KEY,
    TLS_APP,
    all_active_idle,
    get_secret_by_label,
    get_unit_ipv4_address,
)

logger = logging.getLogger(__name__)


def test_build_and_deploy(juju: jubilant.Juju, ui_charm: Path):
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
    juju.deploy(TLS_APP, app=TLS_APP, trust=True)
    juju.deploy(ui_charm, app=APP_NAME, trust=True)

    juju.wait(
        lambda status: jubilant.all_agents_idle(status, APP_NAME, KAFKA_APP),
        delay=3,
        timeout=1200,
        successes=10,
    )

    status = juju.status()
    assert status.apps[APP_NAME].app_status.current == "blocked"
    assert status.apps[CONNECT_APP].app_status.current == "blocked"
    assert status.apps[KAFKA_APP].app_status.current == "active"


def test_integrate(juju: jubilant.Juju):
    # First integrate non-UI apps with Kafka
    juju.integrate(CONNECT_APP, KAFKA_APP)

    juju.wait(
        lambda status: all_active_idle(status, CONNECT_APP, KAFKA_APP),
        delay=3,
        timeout=900,
        successes=10,
    )

    # Now integrate all apps with UI app.
    juju.integrate(APP_NAME, KAFKA_APP)
    juju.integrate(APP_NAME, CONNECT_APP)

    juju.wait(
        lambda status: all_active_idle(status, APP_NAME, CONNECT_APP, KAFKA_APP),
        delay=3,
        timeout=900,
        successes=10,
    )


def test_ui(juju: jubilant.Juju):
    secret_data = get_secret_by_label(juju.model, f"cluster.{APP_NAME}.app", owner=APP_NAME)
    password = secret_data.get(SECRET_KEY)

    if not password:
        raise Exception("Can't fetch the admin user's password.")

    unit_ip = get_unit_ipv4_address(juju.model, f"{APP_NAME}/0")
    url = f"{PROTO}://{unit_ip}:{PORT}"

    login_resp = requests.post(
        f"{url}/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"username": "admin", "password": password},
        verify=False,
    )
    assert login_resp.status_code == 200
    # Successful login would lead to a redirect
    assert len(login_resp.history) > 0

    cookie = login_resp.history[0].cookies
    clusters_resp = requests.get(
        f"{url}/api/clusters",
        headers={"Content-Type": "application/json"},
        cookies=cookie,
        verify=False,
    )

    clusters_json = clusters_resp.json()
    logger.info(f"{clusters_json=}")
    assert len(clusters_json) > 0
    assert clusters_json[0].get("status") == "online"
