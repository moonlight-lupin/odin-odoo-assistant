# Odoo discovers tests through this package.
#
# Only `test_log_utils` is listed: it is Odoo-free (plain unittest over pure
# functions) and so runs under both Odoo's test runner and a bare
# `python -m pytest`.
#
# The other modules here run the addon's real code against a FAKE odoo
# (`_fake_odoo`), which only makes sense when the real one is absent — under
# Odoo's runner those paths are exercised live instead. They are deliberately
# NOT imported here (importing them would raise SkipTest during package import
# and skip everything); pytest discovers them by filename:
#
#   test_generic_tools.py       the write guardrails and tool plumbing
#   test_controller_protocol.py JSON-RPC dispatch at /mcp/v1
#   test_oauth.py               PKCE, code replay, token expiry, redirect URIs
#   test_mcp_log.py             the activity log's write path
#   test_parity.py              this module vs. the independent server
from . import test_log_utils
