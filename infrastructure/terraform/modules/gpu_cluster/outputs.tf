###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# GPU Cluster Module - Outputs
###############################################################################

output "node_group_id" {
  description = "ID of the EKS GPU node group"
  value       = aws_eks_node_group.gpu_inference.id
}

output "node_group_arn" {
  description = "ARN of the EKS GPU node group"
  value       = aws_eks_node_group.gpu_inference.arn
}

output "node_group_name" {
  description = "Name of the EKS GPU node group"
  value       = aws_eks_node_group.gpu_inference.node_group_name
}

output "node_group_status" {
  description = "Status of the EKS GPU node group"
  value       = aws_eks_node_group.gpu_inference.status
}

output "autoscaling_group_name" {
  description = "Name of the underlying Auto Scaling Group for the GPU node group"
  value       = aws_eks_node_group.gpu_inference.resources[0].autoscaling_groups[0].name
}

output "placement_group_id" {
  description = "ID of the placement group for GPU instances"
  value       = aws_placement_group.gpu_cluster.id
}

output "placement_group_name" {
  description = "Name of the placement group for GPU instances"
  value       = aws_placement_group.gpu_cluster.name
}

output "launch_template_id" {
  description = "ID of the launch template for GPU instances"
  value       = aws_launch_template.gpu_nodes.id
}

output "launch_template_latest_version" {
  description = "Latest version number of the GPU launch template"
  value       = aws_launch_template.gpu_nodes.latest_version
}

output "gpu_scale_out_policy_arn" {
  description = "ARN of the GPU utilization-based scale-out policy"
  value       = aws_autoscaling_policy.gpu_scale_out.arn
}

output "latency_scaling_policy_arn" {
  description = "ARN of the inference latency-based scaling policy"
  value       = aws_autoscaling_policy.inference_latency_scaling.arn
}

output "warm_pool_state" {
  description = "State configuration of the warm pool"
  value       = aws_autoscaling_warm_pool.gpu_warm_pool.pool_state
}

output "gpu_utilization_alarm_arn" {
  description = "ARN of the GPU high utilization CloudWatch alarm"
  value       = aws_cloudwatch_metric_alarm.gpu_utilization_high.arn
}

output "gpu_memory_alarm_arn" {
  description = "ARN of the GPU memory high utilization CloudWatch alarm"
  value       = aws_cloudwatch_metric_alarm.gpu_memory_high.arn
}
