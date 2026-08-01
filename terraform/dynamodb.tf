resource "aws_dynamodb_table" "main" {
  name         = local.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_dynamodb_table_item" "config" {
  table_name = aws_dynamodb_table.main.name
  hash_key   = aws_dynamodb_table.main.hash_key
  range_key  = aws_dynamodb_table.main.range_key

  item = jsonencode({
    pk = { S = "CONFIG" }
    sk = { S = "MAIN" }
    start_date = { S = var.default_start_date }
    end_date = { S = var.default_end_date }
    nearly_threshold = { N = "20" }
    notify_email = { S = var.notify_email }
    auto_watch_sold_out = { BOOL = true }
  })

  lifecycle {
    ignore_changes = [item]
  }
}
