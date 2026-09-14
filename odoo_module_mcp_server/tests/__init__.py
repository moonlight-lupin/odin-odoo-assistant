# Odoo discovers tests through this package.
#
# Only `test_log_utils` is listed: it is Odoo-free (plain unittest over pure
# functions) and so runs under both Odoo's test runner and a bare
# `python -m pytest`.
#
# `test_mcp_log` is deliberately NOT imported here. It exercises the log
# model's write path against a fake `odoo`, which only makes sense when the
# real one is absent — under Odoo's runner those behaviours are exercised live
# by any call through /mcp/v1. pytest still discovers it by filename.
from . import test_log_utils
