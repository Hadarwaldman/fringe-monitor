data "aws_caller_identity" "current" {}

locals {
  name            = var.project_name
  account_id      = data.aws_caller_identity.current.account_id
  lambda_zip_path = "${path.module}/../build/lambda.zip"
  frontend_dir    = "${path.module}/../frontend"
}
