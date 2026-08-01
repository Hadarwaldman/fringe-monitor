locals {
  lambda_env = {
    TABLE_NAME          = aws_dynamodb_table.main.name
    DATA_BUCKET         = aws_s3_bucket.data.id
    NOTIFY_EMAIL        = var.notify_email
    FROM_EMAIL          = var.notify_email
    DEFAULT_START_DATE  = var.default_start_date
    DEFAULT_END_DATE    = var.default_end_date
  }

  lambda_hash = fileexists(local.lambda_zip_path) ? filebase64sha256(local.lambda_zip_path) : null
}

resource "aws_lambda_function" "full_scan" {
  function_name = "${local.name}-full-scan"
  role          = aws_iam_role.lambda.arn
  handler       = "full_scan.handler"
  runtime       = "python3.12"
  filename      = local.lambda_zip_path
  source_code_hash = local.lambda_hash

  timeout     = 900
  memory_size = 1024

  environment {
    variables = local.lambda_env
  }

  depends_on = [
    aws_iam_role_policy.lambda_app,
    aws_iam_role_policy_attachment.lambda_basic,
  ]
}

resource "aws_lambda_function" "watchlist" {
  function_name = "${local.name}-watchlist"
  role          = aws_iam_role.lambda.arn
  handler       = "watchlist.handler"
  runtime       = "python3.12"
  filename      = local.lambda_zip_path
  source_code_hash = local.lambda_hash

  timeout     = 900
  memory_size = 1024

  environment {
    variables = local.lambda_env
  }

  depends_on = [
    aws_iam_role_policy.lambda_app,
    aws_iam_role_policy_attachment.lambda_basic,
  ]
}

resource "aws_lambda_function" "api" {
  function_name = "${local.name}-api"
  role          = aws_iam_role.lambda.arn
  handler       = "api.handler"
  runtime       = "python3.12"
  filename      = local.lambda_zip_path
  source_code_hash = local.lambda_hash

  timeout     = 30
  memory_size = 256

  environment {
    variables = merge(local.lambda_env, {
      FULL_SCAN_FUNCTION_NAME = aws_lambda_function.full_scan.function_name
    })
  }

  depends_on = [
    aws_iam_role_policy.lambda_app,
    aws_iam_role_policy_attachment.lambda_basic,
  ]
}

resource "aws_cloudwatch_log_group" "full_scan" {
  name              = "/aws/lambda/${aws_lambda_function.full_scan.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "watchlist" {
  name              = "/aws/lambda/${aws_lambda_function.watchlist.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${aws_lambda_function.api.function_name}"
  retention_in_days = 14
}
