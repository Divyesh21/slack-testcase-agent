"""
AWS Lambda entry point for the Slack Test-Case Bot.

Lazy listener wiring
─────────────────────
slack_bolt's lazy listener pattern requires the Lambda to invoke *itself* a second
time to run the deferred (heavy) work.  It does this via the AWS SDK, using the
function name stored in SLACK_BOLT_LAZY_LAMBDA_FUNCTION_NAME.

AWS_LAMBDA_FUNCTION_NAME is injected automatically by the Lambda runtime, so we
just copy it into the slack_bolt env var before importing anything from app.py.
The execution role must include  lambda:InvokeFunction  on this function's ARN
(the SAM template in template.yaml already provisions this).
"""

import os
import logging

# Wire up lazy-listener self-invocation BEFORE importing app.py.
_fn_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
if _fn_name:
    os.environ.setdefault("SLACK_BOLT_LAZY_LAMBDA_FUNCTION_NAME", _fn_name)

# Now import the bolt app (safe to do here; lazy init means no heavy work yet).
from app import bolt_app  # noqa: E402  (import after env-var setup is intentional)
from slack_bolt.adapter.aws_lambda import SlackRequestHandler  # noqa: E402

SlackRequestHandler.clear_all_log_handlers()
logging.getLogger().setLevel(logging.INFO)

_slack_handler = SlackRequestHandler(app=bolt_app)


def handler(event, context):
    """Lambda handler — called by API Gateway for every incoming Slack request."""
    return _slack_handler.handle(event, context)
