output "oauth_offer_url" {
  description = "Hydra OAuth offer URL, consumed by kafka-ui:oauth."
  value       = module.iam.oauth_offer_url
}

output "oauth_ca_offer_url" {
  description = "self-signed-certificates send-ca-cert offer URL, consumed by kafka-ui:oauth-ca."
  value       = juju_offer.send_ca_cert.url
}

output "core_model_name" {
  description = "Name of the core (dependencies) Juju model."
  value       = juju_model.core.name
}

output "iam_model_name" {
  description = "Name of the identity platform Juju model."
  value       = juju_model.iam.name
}
