resource "juju_model" "core" {
  name = var.core_model_name

  cloud {
    name = var.k8s_cloud_name
  }

  # `juju add-k8s <name>` stores the credential under the cloud's own name
  # (cmd/juju/caas/add.go: `credentialName := c.caasName`). Without this the
  # terraform provider sends an empty credential tag and the controller opens
  # the k8s client with no authentication at all.
  credential = var.k8s_cloud_name
}

resource "juju_application" "certificates" {
  model_uuid = juju_model.core.uuid
  name       = "self-signed-certificates"

  charm {
    name    = "self-signed-certificates"
    channel = var.certificates_channel
    base    = "ubuntu@24.04"
  }

  units = 1
  trust = true

  depends_on = [juju_model.core]
}

resource "juju_application" "traefik_public" {
  model_uuid = juju_model.core.uuid
  name       = "traefik-public"

  charm {
    name    = "traefik-k8s"
    channel = var.traefik_channel
    base    = "ubuntu@20.04"
  }

  units = 1
  trust = true

  depends_on = [juju_model.core, juju_application.certificates]
}

resource "juju_application" "postgresql" {
  model_uuid = juju_model.core.uuid
  name       = "postgresql-k8s"

  charm {
    name    = "postgresql-k8s"
    channel = var.postgresql_channel
    base    = "ubuntu@22.04"
  }

  units = 1
  trust = true

  depends_on = [juju_model.core]
}

resource "juju_model" "iam" {
  name = var.iam_model_name

  cloud {
    name = var.k8s_cloud_name
  }

  # `juju add-k8s <name>` stores the credential under the cloud's own name
  # (cmd/juju/caas/add.go: `credentialName := c.caasName`). Without this the
  # terraform provider sends an empty credential tag and the controller opens
  # the k8s client with no authentication at all.
  credential = var.k8s_cloud_name
}

module "iam" {
  source = "github.com/canonical/iam-bundle-integration?ref=v1.1.1"

  model = juju_model.iam.uuid

  postgresql_offer_url    = juju_offer.postgresql.url
  traefik_route_offer_url = juju_offer.traefik_route.url

  enable_kratos_external_idp_integrator = true
  kratos_external_idp_integrator = {
    config = {
      client_id     = var.idp_client_id
      client_secret = var.idp_client_secret
      issuer_url    = var.idp_issuer_url
      provider      = "generic"
      provider_id   = var.idp_provider_id
      scope         = "profile email"
    }
  }

  depends_on = [juju_model.iam]
}
