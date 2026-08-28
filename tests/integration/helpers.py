#!/usr/bin/env python3
# Copyright 2025 marc
# See LICENSE file for licensing details.

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from subprocess import PIPE, check_output
from typing import Any

import jubilant
import requests
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

IAM_TERRAFORM_DIR = "tests/integration/terraform/iam"
IAM_MODEL = "iam"
CORE_MODEL = "core"
HYDRA_APP = "hydra"
KRATOS_EXTERNAL_IDP_INTEGRATOR_APP = "kratos-external-idp-integrator"
IAM_APPS = ("hydra", "kratos", "login-ui", KRATOS_EXTERNAL_IDP_INTEGRATOR_APP)

ADMIN_USER = AppContext.ADMIN_USERNAME
TEST_SECRET_NAME = "authsecret"
AUTH_SECRET_CONFIG_KEY = "system-users"

PORT = 8080
PROTO = "https"
SECRET_KEY = "admin-password"


def all_active_idle(status: jubilant.Status, *apps: str):
    """Check all units are in active|idle state."""
    return jubilant.all_agents_idle(status, *apps) and jubilant.all_active(status, *apps)


def wait_for_ui_serving(url: str, timeout: int = 600, delay: int = 5) -> None:
    """Block until the Kafka UI actually serves the given URL."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            response = requests.get(url, verify=False, timeout=10)
            if response.status_code < 500:
                logger.info(f"Kafka UI serving {url} ({response.status_code})")
                return
            last = f"HTTP {response.status_code}"
        except requests.RequestException as e:
            last = repr(e)

        logger.info(f"Waiting for Kafka UI at {url} ({last})")
        time.sleep(delay)

    raise TimeoutError(f"Kafka UI did not serve {url} within {timeout}s (last: {last})")


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


def get_controller_credentials(controller: str | None = None) -> dict[str, str]:
    """Get Juju controller credentials for the terraform `juju` provider."""
    cmd = "juju show-controller --show-password"
    if controller:
        cmd = f"juju show-controller {controller} --show-password"

    controller_credentials = yaml.safe_load(
        check_output(
            cmd,
            stderr=PIPE,
            shell=True,
            universal_newlines=True,
        )
    )

    def get_value(obj: dict, key: str):
        """Recursively get the value for the given key in a nested dict."""
        if key in obj:
            return obj.get(key, "")
        for value in obj.values():
            if isinstance(value, dict):
                item = get_value(value, key)
                if item is not None:
                    return item

    return {
        "JUJU_USERNAME": get_value(controller_credentials, "user"),
        "JUJU_PASSWORD": get_value(controller_credentials, "password"),
        "JUJU_CONTROLLER_ADDRESSES": ",".join(get_value(controller_credentials, "api-endpoints")),
        "JUJU_CA_CERT": get_value(controller_credentials, "ca-cert"),
    }


class TerraformDeployer:
    """Minimal helper to drive a terraform module from the integration tests.

    Adapted from
    https://github.com/canonical/kafka-k8s-bundle/blob/main/tests/integration/terraform/helpers.py
    """

    def __init__(self, terraform_dir: str = IAM_TERRAFORM_DIR, controller: str | None = None):
        self.terraform_dir = Path(terraform_dir).resolve()
        self.controller = controller
        self.tfvars_file: str | None = None

    def _env(self) -> dict[str, str]:
        return {**os.environ, **get_controller_credentials(self.controller)}

    def create_tfvars(self, config: dict[str, Any]) -> str:
        """Write a `*.tfvars.json` file with the given configuration."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tfvars.json", delete=False) as handle:
            json.dump(config, handle, indent=2)
            self.tfvars_file = handle.name
        return self.tfvars_file

    def init(self) -> None:
        """Initialize terraform in the module directory."""
        result = subprocess.run(
            ["terraform", "init", "-input=false"],
            cwd=self.terraform_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Terraform init failed: {result.stderr}")
        logger.info(f"Terraform initialized:\n{result.stdout}")

    def apply(self, tfvars_file: str | None = None) -> None:
        """Apply the terraform configuration."""
        cmd = ["terraform", "apply", "-auto-approve", "-input=false"]
        if tfvars_file:
            cmd.append(f"-var-file={tfvars_file}")
        result = subprocess.run(cmd, cwd=self.terraform_dir, text=True, env=self._env())
        if result.returncode != 0:
            raise RuntimeError(f"Terraform apply failed (exit {result.returncode})")

    def destroy(self, tfvars_file: str | None = None) -> None:
        """Destroy the terraform-managed resources."""
        cmd = ["terraform", "destroy", "-auto-approve", "-input=false"]
        if tfvars_file:
            cmd.append(f"-var-file={tfvars_file}")
        result = subprocess.run(cmd, cwd=self.terraform_dir, text=True, env=self._env())
        if result.returncode != 0:
            raise RuntimeError(f"Terraform destroy failed (exit {result.returncode})")

    def output(self) -> dict[str, Any]:
        """Return the terraform outputs as a dict of ``{name: value}``."""
        raw = check_output(
            ["terraform", "output", "-json"],
            cwd=self.terraform_dir,
            text=True,
            env=self._env(),
        )
        return {key: val["value"] for key, val in json.loads(raw).items()}

    def cleanup(self) -> None:
        """Remove the generated tfvars file and local terraform artifacts."""
        if self.tfvars_file and Path(self.tfvars_file).exists():
            Path(self.tfvars_file).unlink()

        shutil.rmtree(self.terraform_dir / ".terraform", ignore_errors=True)
        for pattern in [".terraform.lock.hcl", "terraform.tfstate*", "*.tfplan"]:
            for path in self.terraform_dir.glob(pattern):
                path.unlink(missing_ok=True)
