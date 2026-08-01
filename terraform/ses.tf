resource "aws_sesv2_email_identity" "notify" {
  email_identity = var.notify_email
}
