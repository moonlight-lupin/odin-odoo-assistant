# Odoo discovers tests through this package. `test_log_utils` is deliberately
# Odoo-free (plain unittest over pure functions) so it also runs under a bare
# `python -m pytest odoo_module_mcp_server/tests` with no Odoo installed.
from . import test_log_utils
