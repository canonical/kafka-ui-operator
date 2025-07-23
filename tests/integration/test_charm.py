#!/usr/bin/env python3
# Copyright 2025 marc
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import jubilant
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
CONNECT_APP = "kafka-connect"
CONNECT_CHANNEL = "latest/edge"
KAFKA_APP = "kafka"
KAFKA_CHANNEL = "4/edge"
TLS_APP = "self-signed-certificates"


def all_active_idle(status: jubilant.Status, *apps: str):
    """Check all units are in active|idle state."""
    return jubilant.all_agents_idle(status, *apps) and jubilant.all_active(status, *apps)


def test_build_and_deploy(juju: jubilant.Juju, ui_charm):
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


def test_integrate(juju: jubilant.Juju):
    juju.integrate(APP_NAME, KAFKA_APP)

    juju.wait(
        lambda status: all_active_idle(status, APP_NAME, KAFKA_APP),
        delay=3,
        timeout=900,
        successes=10,
    )
