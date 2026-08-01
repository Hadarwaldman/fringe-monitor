output "cloudfront_url" {
  description = "Public frontend URL"
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "api_url" {
  description = "HTTP API base URL"
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "data_bucket" {
  value = aws_s3_bucket.data.id
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.id
}

output "dynamodb_table" {
  value = aws_dynamodb_table.main.name
}

output "full_scan_lambda" {
  value = aws_lambda_function.full_scan.function_name
}

output "watchlist_lambda" {
  value = aws_lambda_function.watchlist.function_name
}

output "notify_email" {
  value = var.notify_email
}

output "ses_identity_arn" {
  value = aws_sesv2_email_identity.notify.arn
}
