# All scheduled jobs are DISABLED since the end of Fringe 2026 (2026-08-20):
# no scans run, so no proxy bandwidth is consumed. To bring the monitor back
# for a future festival, remove the state = "DISABLED" lines below (watchlist
# stays disabled permanently — see its own comment).
resource "aws_cloudwatch_event_rule" "daily_full_scan" {
  name                = "${local.name}-daily-full-scan"
  description         = "Daily Fringe full programme scan"
  schedule_expression = var.daily_schedule
  state               = "DISABLED"
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

# The whole-programme 15-min watchlist is DISABLED: it re-scanned ~15,000
# performances every cycle and dominated proxy bandwidth. Wishlist freshness is
# now handled cheaply by the wishlist-refresh rule below (only the user's
# PlanMyFringe wishlist shows). The watchlist Lambda + rule are kept (state=
# DISABLED) so they can be re-enabled if ever needed, without recreating them.
resource "aws_cloudwatch_event_rule" "watchlist" {
  name                = "${local.name}-watchlist-15m"
  description         = "DISABLED: old whole-programme watchlist (replaced by wishlist-refresh)"
  schedule_expression = var.watchlist_schedule
  state               = "DISABLED"
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

resource "aws_cloudwatch_event_rule" "wishlist_refresh" {
  name                = "${local.name}-wishlist-refresh"
  description         = "Refresh availability for the PlanMyFringe wishlist shows only (cheap)"
  schedule_expression = var.wishlist_refresh_schedule
  state               = "DISABLED"
}

resource "aws_cloudwatch_event_target" "wishlist_refresh" {
  rule      = aws_cloudwatch_event_rule.wishlist_refresh.name
  target_id = "wishlist-refresh"
  arn       = aws_lambda_function.wishlist_refresh.arn
}

resource "aws_lambda_permission" "wishlist_refresh" {
  statement_id  = "AllowEventBridgeWishlistRefresh"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.wishlist_refresh.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.wishlist_refresh.arn
}

resource "aws_cloudwatch_event_rule" "monitor_check" {
  name                = "${local.name}-monitor-check"
  description         = "Lightweight show-monitor check (cheap, frequent)"
  schedule_expression = var.monitor_schedule
  state               = "DISABLED"
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
