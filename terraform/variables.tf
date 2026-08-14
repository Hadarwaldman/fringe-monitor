variable "aws_region" {
  description = "AWS region for most resources"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile (empty string uses default/env credentials — preferred in CI)"
  type        = string
  default     = "hadar-pc"
}

variable "project_name" {
  description = "Name prefix / tag value"
  type        = string
  default     = "fringe-monitor"
}

variable "notify_email" {
  description = "Email address for reopen alerts (also SES identity)"
  type        = string
  default     = "hadarwaldman@gmail.com"
}

variable "default_start_date" {
  type    = string
  default = "2026-08-12"
}

variable "default_end_date" {
  type    = string
  default = "2026-08-20"
}

variable "edfringe_creds_param" {
  description = <<-EOT
    SSM SecureString parameter holding edfringe account credentials as JSON
    ({"email": "...", "password": "..."}). Created manually (never in TF state):
    aws ssm put-parameter --name /fringe-monitor/edfringe-credentials \
      --type SecureString --value '{"email":"...","password":"..."}'
    Ticket holds are skipped gracefully while it does not exist.
  EOT
  type        = string
  default     = "/fringe-monitor/edfringe-credentials"
}

variable "daily_schedule" {
  description = "EventBridge schedule for full scan (UTC)"
  type        = string
  default     = "cron(0 6 * * ? *)" # 06:00 UTC daily
}

variable "watchlist_schedule" {
  description = "EventBridge schedule for watchlist checks"
  type        = string
  default     = "rate(15 minutes)"
}
