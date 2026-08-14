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

variable "fringe_proxy_param" {
  description = <<-EOT
    SSM SecureString parameter holding the residential proxy URL
    (http://user:pass@host:port) used for edfringe API calls from Lambda —
    the API's Cloudflare front 403s AWS datacenter IPs. Created manually
    (never in TF state):
    aws ssm put-parameter --name /fringe-monitor/proxy-url \
      --type SecureString --value 'http://user:pass@geo.iproyal.com:12321'
    When absent, Lambdas fall back to direct egress (which Cloudflare blocks).
  EOT
  type        = string
  default     = "/fringe-monitor/proxy-url"
}

variable "planmyfringe_creds_param" {
  description = <<-EOT
    SSM SecureString parameter holding PlanMyFringe account credentials as
    JSON ({"user_id": "...", "password": "..."}). Created manually (never in
    TF state):
    aws ssm put-parameter --name /fringe-monitor/planmyfringe-credentials \
      --type SecureString --value '{"user_id":"...","password":"..."}'
    The /planner/sync endpoint returns 409 while it does not exist.
  EOT
  type        = string
  default     = "/fringe-monitor/planmyfringe-credentials"
}

variable "daily_schedule" {
  description = "EventBridge schedule for full scan (UTC)"
  type        = string
  default     = "cron(0 6 * * ? *)" # 06:00 UTC daily
}

variable "watchlist_schedule" {
  description = "EventBridge schedule for watchlist reopen checks (heavy, full programme)"
  type        = string
  default     = "rate(15 minutes)"
}

variable "monitor_schedule" {
  description = "EventBridge schedule for the lightweight show-monitor check"
  type        = string
  default     = "rate(3 minutes)"
}
