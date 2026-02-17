#!/usr/bin/env python3
# Copyright 2025 marc
# See LICENSE file for licensing details.

import json
import logging
import re
from pathlib import Path
from subprocess import PIPE, check_output

import jubilant
import yaml

from core.models import AppContext

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
CONNECT_APP = "kafka-connect"
CONNECT_CHANNEL = "latest/edge"
KAFKA_APP = "kafka"
KAFKA_CHANNEL = "4/edge"
KARAPACE_APP = "karapace"
KARAPACE_CHANNEL = "latest/edge"
TLS_APP = "self-signed-certificates"
TLS_CHANNEL = "1/stable"
ADMIN_USER = AppContext.ADMIN_USERNAME
TEST_SECRET_NAME = "authsecret"
AUTH_SECRET_CONFIG_KEY = "system-users"

PORT = 8080
PROTO = "https"
SECRET_KEY = "admin-password"


def all_active_idle(status: jubilant.Status, *apps: str):
    """Check all units are in active|idle state."""
    return jubilant.all_agents_idle(status, *apps) and jubilant.all_active(status, *apps)


def get_secret_by_label(model: str, label: str, owner: str) -> dict[str, str]:
    secrets_meta_raw = check_output(
        f"JUJU_MODEL={model} juju list-secrets --format json",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    ).strip()
    secrets_meta = json.loads(secrets_meta_raw)

    for secret_id in secrets_meta:
        if owner and not secrets_meta[secret_id]["owner"] == owner:
            continue
        if secrets_meta[secret_id]["label"] == label:
            break

    secrets_data_raw = check_output(
        f"JUJU_MODEL={model} juju show-secret --format json --reveal {secret_id}",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )

    secret_data = json.loads(secrets_data_raw)
    return secret_data[secret_id]["content"]["Data"]


def get_unit_ipv4_address(model_full_name: str | None, unit_name: str) -> str | None:
    """Get unit's IPv4 address.

    This is a safer alternative for `juju.unit.get_public_address()`.
    This function is robust to network changes.
    """
    stdout = check_output(
        f"JUJU_MODEL={model_full_name} juju ssh {unit_name} hostname -i",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )
    ipv4_matches = re.findall(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", stdout)

    if ipv4_matches:
        return ipv4_matches[0]

    return None


def set_password(
    juju: jubilant.Juju, username: str = ADMIN_USER, password: str = "testpass"
) -> None:
    """Use the charm `system-users` config option to start a password rotation."""
    custom_auth = {username: password, "foo": "bar"}
    secret_id = juju.add_secret(name=TEST_SECRET_NAME, content=custom_auth)
    # grant access to our app
    juju.grant_secret(TEST_SECRET_NAME, app=APP_NAME)
    # configure the app to use the secret_id
    juju.config(APP_NAME, values={AUTH_SECRET_CONFIG_KEY: secret_id})
