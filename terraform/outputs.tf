# CC006 mandatory outputs
output "app_name" {
  description = "Name of the Kafka UI application"
  value       = juju_application.ui.name
}

output "endpoints" {
  description = "Service access endpoints"
  value = {
    # Add actual service URLs here if available
    # Could include REST API endpoints, etc.
  }
}

output "provides_endpoints" {
  description = "Relation endpoints this charm provides"
  value = [
    "certificates"
  ]
}
