variable "idp_client_id" {
  description = "OAuth client_id registered on the external IdP (Dex)."
  type        = string
}

variable "idp_client_secret" {
  description = "OAuth client_secret registered on the external IdP (Dex)."
  type        = string
  sensitive   = true
}

variable "idp_issuer_url" {
  description = "Issuer URL of the external IdP (Dex)."
  type        = string
}

variable "idp_provider_id" {
  description = "Provider id exposed on the login UI. Must match the login button text."
  type        = string
  default     = "Dex"
}

variable "core_model_name" {
  description = "Name of the Juju model hosting the IAM dependencies (postgresql, traefik, certificates)."
  type        = string
  default     = "core"
}

variable "iam_model_name" {
  description = "Name of the Juju model hosting the identity platform."
  type        = string
  default     = "iam"
}

variable "postgresql_channel" {
  description = "Charm channel for postgresql-k8s."
  type        = string
  default     = "14/stable"
}

variable "traefik_channel" {
  description = "Charm channel for traefik-k8s (public ingress)."
  type        = string
  default     = "latest/stable"
}

variable "certificates_channel" {
  description = "Charm channel for self-signed-certificates."
  type        = string
  default     = "1/stable"
}
