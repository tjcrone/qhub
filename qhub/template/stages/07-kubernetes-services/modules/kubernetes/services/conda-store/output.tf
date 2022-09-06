output "endpoint" {
  description = "Endpoint dns name of conda-store nfs server"
  value       = "${kubernetes_service.nfs.metadata.0.name}.${var.namespace}.svc.cluster.local"
}

output "endpoint_ip" {
  description = "IP Address of conda-store nfs server"
  value       = kubernetes_service.nfs.spec.0.cluster_ip
}

output "service-tokens" {
  description = "Service tokens for conda-store"
  value       = { for k, _ in var.services: k => base64encode(random_password.conda_store_service_token[k].result) }
}