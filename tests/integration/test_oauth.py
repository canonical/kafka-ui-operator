#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import re

import jubilant
import pytest
import requests
from helpers import (
    APP_NAME,
    IAM_APPS,
    IAM_MODEL,
    KAFKA_APP,
    KAFKA_CHANNEL,
    KRATOS_EXTERNAL_IDP_INTEGRATOR_APP,
    PORT,
    PROTO,
    TLS_APP,
    TLS_CHANNEL,
    TerraformDeployer,
    all_active_idle,
    get_unit_ipv4_address,
    wait_for_ui_serving,
)
from oauth_tools import (
    access_application_login_page,
    click_on_sign_in_button_by_text,
    get_cookies_from_browser_by_url,
)
from oauth_tools.constants import EXTERNAL_USER_EMAIL
from oauth_tools.external_idp import DexIdpService
from playwright.async_api._generated import BrowserContext, Page
from pytest_operator.plugin import OpsTest

pytest_plugins = ["oauth_tools.fixtures"]

logger = logging.getLogger(__name__)

# OAuth login requires TLS, and the identity platform requires the k8s cloud that only the
# TLS job sets up. `pytest_collection_modifyitems` turns this into a skip without `--tls`.
pytestmark = pytest.mark.tls_only

DEX_PROVIDER_ID = "Dex"


@pytest.fixture(scope="module")
def iam_deployer(lxd_controller: str | None):
    """Own the lifecycle of the terraform-managed identity platform."""
    deployer = TerraformDeployer(controller=lxd_controller)
    deployer.cleanup()
    yield deployer


@pytest.fixture(scope="module")
def iam_juju(lxd_controller: str | None) -> jubilant.Juju:
    """Juju client for the identity platform model, on the k8s cloud."""
    model = f"{lxd_controller}:{IAM_MODEL}" if lxd_controller else IAM_MODEL
    return jubilant.Juju(model=model)


async def _cross_model_integrate(
    ops_test: OpsTest, offer_url: str, endpoint: str, saas_alias: str
) -> None:
    """Consume a cross-model offer and integrate it into the test model."""
    model = ops_test.model_full_name
    await ops_test.run("juju", "consume", "-m", model, offer_url, saas_alias, check=True)
    await ops_test.run("juju", "integrate", "-m", model, endpoint, saas_alias, check=True)


async def _complete_dex_login(page: Page, ext_idp_service: DexIdpService) -> None:
    """From the identity-platform login UI, log in through the external Dex IdP."""
    async with page.expect_navigation():
        await page.get_by_role("button", name=DEX_PROVIDER_ID).click()

    await ext_idp_service.complete_user_login(page)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest,
    ui_charm,
    ext_idp_service: DexIdpService,
    iam_deployer: TerraformDeployer,
    iam_juju: jubilant.Juju,
):
    tfvars = iam_deployer.create_tfvars(
        {
            "idp_client_id": ext_idp_service.client_id,
            "idp_client_secret": ext_idp_service.client_secret,
            "idp_issuer_url": ext_idp_service.issuer_url,
            "idp_provider_id": DEX_PROVIDER_ID,
        }
    )
    iam_deployer.init()
    iam_deployer.apply(tfvars)
    outputs = iam_deployer.output()

    iam_juju.wait(
        lambda status: all_active_idle(status, *IAM_APPS),
        delay=10,
        successes=3,
        timeout=2000,
    )

    # Register Kratos's OIDC callback URI on the external IdP (Dex). Kratos is an
    # OIDC client of Dex, which rejects the login unless this redirect URI is in its allow-list.
    logger.info("Registering the redirect URI on the external provider")
    task = iam_juju.run(f"{KRATOS_EXTERNAL_IDP_INTEGRATOR_APP}/0", "get-redirect-uri")
    ext_idp_service.update_redirect_uri(redirect_uri=task.results["redirect-uri"])

    # Deploy KafkaUI
    await asyncio.gather(
        ops_test.model.deploy(
            KAFKA_APP,
            application_name=KAFKA_APP,
            channel=KAFKA_CHANNEL,
            trust=True,
            config={"roles": "broker,controller"},
        ),
        ops_test.model.deploy(
            ui_charm,
            application_name=APP_NAME,
            trust=True,
            config={"roles-mapping": f'{{"{EXTERNAL_USER_EMAIL}": "admin"}}'},
        ),
        ops_test.model.deploy(
            TLS_APP,
            application_name=TLS_APP,
            channel=TLS_CHANNEL,
            trust=True,
        ),
    )

    await ops_test.model.wait_for_idle(
        apps=[KAFKA_APP],
        status="active",
        raise_on_blocked=False,
        raise_on_error=False,
        timeout=1200,
    )

    await ops_test.model.integrate(APP_NAME, KAFKA_APP)
    await ops_test.model.integrate(f"{KAFKA_APP}:certificates", TLS_APP)
    await ops_test.model.integrate(f"{APP_NAME}:certificates", TLS_APP)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, KAFKA_APP, TLS_APP],
        status="active",
        raise_on_blocked=False,
        raise_on_error=False,
        timeout=1200,
    )

    await _cross_model_integrate(
        ops_test, outputs["oauth_offer_url"], f"{APP_NAME}:oauth", "hydra"
    )
    await _cross_model_integrate(
        ops_test, outputs["oauth_ca_offer_url"], f"{APP_NAME}:oauth-ca", "oauth-ca"
    )

    # ensuring update-status fires
    async with ops_test.fast_forward(fast_interval="10s"):
        await asyncio.sleep(30)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, KAFKA_APP, TLS_APP],
        status="active",
        raise_on_blocked=False,
        raise_on_error=False,
        timeout=1000,
    )


async def test_oauth_login_with_identity_bundle(
    ops_test: OpsTest,
    page: Page,
    context: BrowserContext,
    ext_idp_service: DexIdpService,
) -> None:
    unit_ip = get_unit_ipv4_address(ops_test.model_full_name, f"{APP_NAME}/0")
    url = f"{PROTO}://{unit_ip}:{PORT}"

    wait_for_ui_serving(url)

    # Kafka UI has a single OAuth provider
    await access_application_login_page(page=page, url=f"{url}/login")

    # Click your application's login button
    await click_on_sign_in_button_by_text(page=page, text="Log in with iam")

    await _complete_dex_login(page=page, ext_idp_service=ext_idp_service)

    # Wait for the OAuth redirect chain to return to the Kafka UI
    await page.wait_for_url(re.compile(re.escape(url)))

    cookies = await get_cookies_from_browser_by_url(context, url + "/")
    session = requests.Session()
    for cookie in cookies:
        session.cookies.set(cookie["name"], cookie["value"])
    clusters_resp = session.get(f"{url}/api/clusters", verify=False)
    clusters_json = clusters_resp.json()

    logger.info(f"{clusters_json=}")
    assert clusters_json
    assert clusters_json[0].get("status") == "online"
