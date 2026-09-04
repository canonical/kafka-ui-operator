#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import random
import time
from pathlib import Path

import jubilant
import requests
from helpers import (
    APP_NAME,
    HAPROXY_APP,
    INGRESS_CONFIGURATOR_APP,
    KAFKA_APP,
    KAFKA_CHANNEL,
    SECRET_KEY,
    TEST_HOSTNAME,
    TLS_APP,
    TLS_CHANNEL,
    all_active_idle,
    deploy_ha_apps,
    get_secret_by_label,
    get_unit_ipv4_address,
)
from tenacity import Retrying, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)


def _assert_login_using_hostname(juju: jubilant.Juju):
    """Assert user can log in using the hostname.

    An alternative way to test this is to edit /etc/hosts,
    and send the request directly to https://{TEST_HOSTNAME}.
    """
    secret_data = get_secret_by_label(juju.model, f"cluster.{APP_NAME}.app", owner=APP_NAME)
    password = secret_data.get(SECRET_KEY)

    haproxy_unit = next(iter(juju.status().apps[HAPROXY_APP].units.keys()))
    haproxy_ip = get_unit_ipv4_address(juju.model, haproxy_unit)
    url = f"https://{haproxy_ip}"
    login_resp = requests.post(
        f"{url}/login",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Hostname": TEST_HOSTNAME,
        },
        data={"username": "admin", "password": password},
        verify=False,
    )

    assert login_resp.status_code == 200


def test_deploy_ui_and_kafka_active(juju: jubilant.Juju, ui_charm: Path, tls_enabled: bool):
    juju.deploy(
        KAFKA_APP,
        app=KAFKA_APP,
        trust=True,
        channel=KAFKA_CHANNEL,
        config={"roles": "broker,controller"},
    )
    juju.deploy(ui_charm, app=APP_NAME, trust=True)
    juju.integrate(APP_NAME, KAFKA_APP)

    if tls_enabled:
        juju.deploy(TLS_APP, app=TLS_APP, channel=TLS_CHANNEL, trust=True)
        juju.integrate(TLS_APP, f"{KAFKA_APP}:certificates")
        juju.integrate(TLS_APP, f"{APP_NAME}:certificates")

    juju.wait(
        lambda status: all_active_idle(status, APP_NAME, KAFKA_APP),
        delay=3,
        timeout=1200,
        successes=10,
    )


def test_scale_with_no_ingress(juju: jubilant.Juju):
    juju.add_unit(APP_NAME, num_units=2)
    time.sleep(30)

    juju.wait(
        lambda status: jubilant.all_agents_idle(status, APP_NAME, KAFKA_APP),
        delay=3,
        timeout=900,
        successes=10,
    )

    status = juju.status()
    # missing ingress relation should lead to blocked status
    assert status.apps[APP_NAME].app_status.current == "blocked"


def test_activate_ingress(juju: jubilant.Juju, tls_enabled: bool):
    deploy_ha_apps(juju, tls_deployed=tls_enabled)
    juju.integrate(APP_NAME, INGRESS_CONFIGURATOR_APP)

    juju.wait(
        lambda status: all_active_idle(status, APP_NAME, INGRESS_CONFIGURATOR_APP, HAPROXY_APP),
        delay=3,
        timeout=900,
        successes=15,
    )

    _assert_login_using_hostname(juju=juju)


# Standard DP HA testing does not apply here, as this is a stateless, uncoordinated workload.
# Any test will be a test of HAProxy charm/workload.
# Nevertheless, this simple test is to ensure that the service is up,
# even if we have one unit available.
def test_min_units_availability(juju: jubilant.Juju):
    # suppress update-status waking up units
    juju.model_config({"update-status-hook-interval": "1000m"})

    status = juju.status()
    to_keep = random.choice(list(status.apps[APP_NAME].units))
    logger.info(f"Killing all units but {to_keep}")
    for unit in status.apps[APP_NAME].units:
        if unit != to_keep:
            juju.ssh(unit, "sudo snap stop charmed-kafka-ui")

    time.sleep(30)
    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(10), reraise=True):
        with attempt:
            _assert_login_using_hostname(juju=juju)
