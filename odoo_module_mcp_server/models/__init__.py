# OAuth 2.1 token store (custom.mcp.oauth.* models). Loaded first so their
# ir.model records + xmlids exist before security/ir.model.access.csv loads.
from . import oauth_client
from . import oauth_authorization
from . import oauth_token
from . import res_config_settings
from . import ir_http
