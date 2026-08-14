resource "aws_cloudwatch_event_rule" "daily_full_scan" {
  name                = "${local.name}-daily-full-scan"
  description         = "Daily Fringe full programme scan"
  schedule_expression = var.daily_schedule
}

resource "aws_cloudwatch_event_target" "daily_full_scan" {
  rule      = aws_cloudwatch_event_rule.daily_full_scan.name
  target_id = "full-scan"
  arn       = aws_lambda_function.full_scan.arn
}

resource "aws_lambda_permission" "daily_full_scan" {
  statement_id  = "AllowEventBridgeDaily"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.full_scan.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_full_scan.arn
}

resource "aws_cloudwatch_event_rule" "watchlist" {
  name                = "${local.name}-watchlist-15m"
  description         = "Watchlist reopen check every 15 minutes"
  schedule_expression = var.watchlist_schedule
}

resource "aws_cloudwatch_event_target" "watchlist" {
  rule      = aws_cloudwatch_event_rule.watchlist.name
  target_id = "watchlist"
  arn       = aws_lambda_function.watchlist.arn
}

resource "aws_lambda_permission" "watchlist" {
  statement_id  = "AllowEventBridgeWatchlist"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.watchlist.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.watchlist.arn
}

resource "aws_cloudwatch_event_rule" "monitor_check" {
  name                = "${local.name}-monitor-check"
  description         = "Lightweight show-monitor check (cheap, frequent)"
  schedule_expression = var.monitor_schedule
}

resource "aws_cloudwatch_event_target" "monitor_check" {
  rule      = aws_cloudwatch_event_rule.monitor_check.name
  target_id = "monitor-check"
  arn       = aws_lambda_function.monitor_check.arn
}

resource "aws_lambda_permission" "monitor_check" {
  statement_id  = "AllowEventBridgeMonitorCheck"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.monitor_check.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.monitor_check.arn
}
