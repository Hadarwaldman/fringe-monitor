data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_app" {
  statement {
    sid = "S3Data"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.data.arn,
      "${aws_s3_bucket.data.arn}/*",
    ]
  }

  statement {
    sid = "Dynamo"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchWriteItem",
    ]
    resources = [aws_dynamodb_table.main.arn]
  }

  statement {
    sid       = "SES"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["*"]
  }

  statement {
    sid     = "InvokeFullScan"
    actions = ["lambda:InvokeFunction"]
    # Constructed ARNs avoid a cycle with the lambda resources
    resources = [
      "arn:aws:lambda:${var.aws_region}:${local.account_id}:function:${local.name}-full-scan",
      "arn:aws:lambda:${var.aws_region}:${local.account_id}:function:${local.name}-watchlist",
    ]
  }

  statement {
    sid     = "EdfringeCreds"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${var.edfringe_creds_param}",
      "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${var.fringe_proxy_param}",
    ]
  }

  statement {
    sid       = "EdfringeCredsDecrypt"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "lambda_app" {
  name   = "${local.name}-lambda-app"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_app.json
}
