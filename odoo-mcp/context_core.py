"""Odoo context served from a Knowledge workspace — the shared core.

ONE file, byte-identical in both servers:

* ``odoo_module_mcp_server/context_core.py`` (the in-Odoo module), and
* ``odoo-mcp/context_core.py`` (the independent XML-RPC server).

Both servers' parity tests fail if the two copies differ, so the two routes
give the same answer for the same question. Each server supplies a small
*backend* (see :class:`Backend`) that does the actual Odoo calls — the ORM
in-process, or execute_kw over XML-RPC — and everything else (routing, the
company filter, journal resolution, drift and stale checks, the proposal
flow, HTML↔markdown) lives here, in plain Python with no Odoo import.

Content model (see the spec, "Odoo context served from Knowledge"):

* All context sits under one Knowledge workspace article (the *root*), whose
  id is a setting. Articles are found by their ``ctx_*`` article properties,
  never by title or position.
* Knowledge applies a property definition to the direct children of the
  article that holds it, so every parent article carries the same definition
  (:data:`PROPERTY_DEFINITION`).
* Reads return approved articles only, filtered to the companies the user is
  allowed into (Knowledge itself has no company field).
* Writes go through a proposal: ``odoo_context_propose`` files one under the
  Proposals folder; ``odoo_context_apply_proposal`` (reviewers) applies or
  rejects it and appends a change-log row.
"""
import datetime
import html
import json
import re
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

MODEL = 'knowledge.article'

CONTRIBUTOR_GROUP = 'odoo_module_mcp_server.group_context_contributor'
REVIEWER_GROUP = 'odoo_module_mcp_server.group_context_reviewer'

DEFAULT_STALE_DAYS = 90

#: Article types. ``proposals`` and ``rejected`` mark the two folder articles
#: the write flow files proposals into; ``proposal`` is one proposal.
TYPES = ('entity', 'rulebook', 'journals', 'chart', 'taxes', 'banking',
         'partners', 'fs_section', 'structure', 'guide', 'log',
         'proposal', 'proposals', 'rejected')
STATUSES = ('approved', 'proposed', 'rejected')

#: The structure-level article types routed from a structure article.
STRUCTURE_PARTS = ('journals', 'chart', 'taxes', 'banking', 'partners')

#: Which article types each task routes. No task = everything.
TASK_TYPES = {
    'posting': {'guide', 'entity', 'rulebook', 'journals', 'chart', 'taxes', 'partners'},
    'bills': {'guide', 'entity', 'rulebook', 'journals', 'chart', 'taxes', 'partners'},
    'bank': {'guide', 'entity', 'rulebook', 'journals', 'banking'},
    'elimination': {'guide', 'entity', 'rulebook', 'journals', 'chart', 'partners'},
    'review': {'guide', 'entity', 'rulebook', 'journals', 'chart', 'taxes', 'banking', 'partners'},
    'reporting': {'guide', 'entity', 'rulebook', 'journals', 'chart'},
}
ALL_ROUTED = TASK_TYPES['review']

#: Types never offered as a proposal target.
NOT_PROPOSABLE = ('log', 'proposal', 'proposals', 'rejected')

#: The property definition every parent article in the workspace carries.
#: Selection keys equal their labels so domains and reads use stable values.
PROPERTY_DEFINITION = [
    {'name': 'ctx_type', 'string': 'Context type', 'type': 'selection',
     'selection': [[t, t] for t in TYPES]},
    {'name': 'ctx_company_id', 'string': 'Company id', 'type': 'integer'},
    {'name': 'ctx_structure', 'string': 'Structure', 'type': 'char'},
    {'name': 'ctx_status', 'string': 'Status', 'type': 'selection',
     'selection': [[s, s] for s in STATUSES]},
    {'name': 'ctx_verified_on', 'string': 'Verified on', 'type': 'date'},
    {'name': 'ctx_verified_via', 'string': 'Verified via', 'type': 'char'},
    {'name': 'ctx_year_end', 'string': 'Year end (DD-MM)', 'type': 'char'},
    {'name': 'ctx_parent_id', 'string': 'Parent company id', 'type': 'integer'},
    {'name': 'ctx_currency', 'string': 'Currency', 'type': 'char'},
    {'name': 'ctx_target', 'string': 'Target article', 'type': 'many2one',
     'comodel': MODEL},
]
PROPERTY_NAMES = tuple(p['name'] for p in PROPERTY_DEFINITION)

ARTICLE_FIELDS = ['id', 'name', 'parent_id', 'parent_path', 'article_properties',
                  'active', 'create_uid']

#: Methods that only read — allowed on workspace articles through the generic
#: tools. Everything else is refused by the workspace write guard.
READ_METHODS = frozenset({
    'read', 'search', 'search_read', 'search_count', 'read_group',
    'fields_get', 'get_views', 'name_search', 'default_get', 'exists',
    'check_access_rights', 'check_access_rule', 'check_access', 'has_access',
    'web_read', 'web_search_read',
})

#: Models whose writes can change a workspace article (the article itself, and
#: its member/permission rows).
GUARDED_MODELS = {
    MODEL: 'id',
    'knowledge.article.member': 'article_id',
}

_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
           'Oct', 'Nov', 'Dec')
_MONTH_NAMES = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
                'August', 'September', 'October', 'November', 'December')


#: Tool descriptions, shared so both servers advertise the same text.
TOOL_DOCS = {
    'odoo_context_for': (
        "Start every Odoo task here. Given an Odoo company id, returns that "
        "company's live facts (parent, root, year end, currency, opening and "
        "lock dates), its journals resolved live (own journal, or the nearest "
        "ancestor's shared journal with the posting procedure; bank journals "
        "are only ever the company's own; the consolidation journal CJE where "
        "one applies), and the approved context articles for it from the "
        "Knowledge workspace (Read me first, the entity article, its rulebook, "
        "and its structure's journals / chart / taxes / banking / partners "
        "pages) as markdown, plus warnings: year-end closes, drift between "
        "Odoo and the entity article, stale or missing articles. `task` trims "
        "the articles to what the task needs. Refused for companies you are "
        "not allowed into."),
    'odoo_context_lookup': (
        "Search the Odoo context workspace or read one article. Pass `query` "
        "for a ranked list (id, title, type, company, verified-on, snippet) — "
        "optionally filtered by `structure` or `ctx_type`; proposals are left "
        "out unless `include_proposals` is true. Or pass `article_id` to get "
        "that article in full as markdown with its properties. Pass exactly "
        "one of the two. Only articles for your allowed companies are returned."),
    'odoo_context_propose': (
        "Propose a change to the Odoo context workspace. Nothing approved is "
        "changed: this files a proposal for a context reviewer. Give either "
        "`target_article_id` (an approved article to replace) or `new` "
        "({parent_id, title, properties}) for a new article, plus `change` — "
        "the COMPLETE new article body in markdown, not a diff — `evidence` "
        "(the Odoo calls or documents that support it) and `reason`. Requires "
        "the Context contributor group."),
    'odoo_context_apply_proposal': (
        "Approve or reject a context proposal (Context reviewer group only). "
        "Approve writes the proposal's text into the target article (or "
        "creates the new article), sets its verified-on date to today and "
        "verified-via to the proposal's evidence, appends a Change log row "
        "and marks the proposal approved. Reject marks it rejected and files "
        "it under Proposals → Rejected; the target is untouched."),
}

#: JSON schemas for the in-Odoo module. The independent server derives the
#: same shape from its function signatures (checked by the parity tests).
TOOL_SCHEMAS = {
    'odoo_context_for': {
        'type': 'object',
        'properties': {
            'company_id': {'type': 'integer', 'description': 'Odoo company id.'},
            'task': {'type': 'string', 'enum': sorted(TASK_TYPES),
                     'description': 'Optional: trims the articles to this task.'},
        },
        'required': ['company_id'],
        'additionalProperties': False,
    },
    'odoo_context_lookup': {
        'type': 'object',
        'properties': {
            'query': {'type': 'string', 'description': 'Text to search for.'},
            'article_id': {'type': 'integer', 'description': 'Read this article in full.'},
            'structure': {'type': 'string', 'description': 'Only this structure, e.g. shc-ii.'},
            'ctx_type': {'type': 'string', 'enum': list(TYPES)},
            'include_proposals': {'type': 'boolean', 'default': False},
            'limit': {'type': 'integer', 'default': 10},
        },
        'additionalProperties': False,
    },
    'odoo_context_propose': {
        'type': 'object',
        'properties': {
            'change': {'type': 'string',
                       'description': 'The complete new article body, in markdown.'},
            'evidence': {'type': 'string'},
            'reason': {'type': 'string'},
            'target_article_id': {'type': 'integer'},
            'new': {'type': 'object',
                    'description': '{parent_id, title, properties} for a new article.'},
        },
        'required': ['change', 'evidence', 'reason'],
        'additionalProperties': False,
    },
    'odoo_context_apply_proposal': {
        'type': 'object',
        'properties': {
            'proposal_id': {'type': 'integer'},
            'decision': {'type': 'string', 'enum': ['approve', 'reject']},
            'note': {'type': 'string'},
        },
        'required': ['proposal_id', 'decision'],
        'additionalProperties': False,
    },
}


class ContextError(ValueError):
    """A refusal or bad input. The message is shown to the agent as-is."""


class ContextPolicyError(ContextError):
    """A guardrail refusal (filed as 'blocked' in the activity logs)."""


class Backend:
    """What a server must provide. All calls run AS THE CALLING USER except
    :meth:`create` with ``elevated=True``.

    ``search_read``/``read`` return plain dicts as Odoo's ``read`` does: a
    many2one is ``[id, name]`` or ``False``; ``article_properties`` is the list
    of property dicts, each with its ``value``.
    """

    def search_read(self, model, domain, fields, order=None, limit=None,
                    all_companies=False):
        raise NotImplementedError

    def create(self, model, values, elevated=False):
        raise NotImplementedError

    def write(self, model, ids, values):
        raise NotImplementedError

    def post_message(self, article_id, body_html, partner_ids=()):
        raise NotImplementedError

    def user(self):
        """``{'id', 'name', 'company_ids': [...]}`` for the calling user."""
        raise NotImplementedError

    def has_group(self, xmlid):
        raise NotImplementedError

    def group_partner_ids(self, xmlid):
        """Partner ids of the group's users, for notifications. May return []."""
        return []

    def today(self):
        return datetime.date.today()

    def base_url(self):
        return ''

    def root_article_id(self):
        """The configured workspace root article id, or None."""
        raise NotImplementedError

    def stale_days(self):
        return DEFAULT_STALE_DAYS


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _m2o_id(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    if isinstance(value, dict):
        return value.get('id')
    return value or None


def _m2o_name(value):
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return value[1]
    return None


def props(article):
    """``{name: value}`` for an article's ``ctx_*`` properties."""
    out = {}
    for prop in article.get('article_properties') or []:
        if isinstance(prop, dict) and prop.get('name') in PROPERTY_NAMES:
            value = prop.get('value')
            if prop.get('type') == 'many2one':
                value = _m2o_id(value)
            out[prop['name']] = value if value not in (None, '') else False
    return out


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def fmt_date(value):
    """DD MMM YYYY, house style."""
    d = _parse_date(value)
    return '%02d %s %d' % (d.day, _MONTHS[d.month - 1], d.year) if d else None


def _path_ids(parent_path):
    return [int(p) for p in (parent_path or '').split('/') if p.strip().isdigit()]


def _int(value, name):
    if isinstance(value, bool) or value is None:
        raise ContextError('%s is required and must be an integer.' % name)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ContextError('%s must be an integer, got %r.' % (name, value))


# ---------------------------------------------------------------------------
# HTML → markdown (stdlib only, so both servers produce identical output)
# ---------------------------------------------------------------------------

_BLOCK = {'p', 'div', 'section', 'article', 'header', 'footer', 'h1', 'h2',
          'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'table', 'tr', 'pre',
          'blockquote', 'hr', 'br'}
_SKIP = {'script', 'style', 'head', 'title', 'template', 'button', 'svg'}


class _MarkdownWriter(HTMLParser):
    """A small, predictable HTML→markdown converter for Knowledge bodies.

    Handles what context articles use: headings, paragraphs, bold/italic,
    inline code and code blocks, links, nested lists, tables, quotes and rules.
    Anything else contributes its text. Embedded Knowledge views and other
    widgets reduce to their text (or nothing).
    """

    def __init__(self, base_url=''):
        super().__init__(convert_charrefs=True)
        self.base_url = (base_url or '').rstrip('/')
        self.out = []            # finished blocks
        self.line = []           # inline text of the current block
        self.lists = []          # stack of ['ul'|'ol', counter]
        self.skip = 0
        self.pre = 0
        self.quote = 0
        self.links = []          # stack of hrefs
        self.table = None        # list of rows (list of cells)
        self.cell = None
        self.heading = 0
        self.li_prefix = None

    # -- block plumbing -------------------------------------------------
    def _flush(self):
        text = ''.join(self.line)
        self.line = []
        if self.pre:
            return
        text = re.sub(r'[ \t\r\n]+', ' ', text).strip()
        if not text:
            self.li_prefix = None
            return
        if self.heading:
            text = '#' * self.heading + ' ' + text
        if self.li_prefix is not None:
            text = self.li_prefix + text
            self.li_prefix = None
            self.out.append(('li', text))
            return
        if self.quote:
            text = '> ' + text
        self.out.append(('p', text))

    def _emit_raw(self, kind, text):
        self.out.append((kind, text))

    # -- parser hooks ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if self.skip or tag in _SKIP:
            if tag in _SKIP:
                self.skip += 1
            return
        attrs = dict(attrs)
        if self.cell is not None:
            if tag == 'br':
                self.cell.append(' ')
            elif tag == 'a':
                self.links.append(attrs.get('href') or '')
                self.cell.append('[')
            elif tag in ('strong', 'b'):
                self.cell.append('**')
            elif tag in ('em', 'i'):
                self.cell.append('*')
            elif tag == 'code':
                self.cell.append('`')
            elif tag in ('td', 'th'):
                self._end_cell()
                self.cell = []
            return
        if tag == 'table':
            self._flush()
            self.table = []
            return
        if self.table is not None:
            if tag == 'tr':
                self.table.append([])
            elif tag in ('td', 'th'):
                if not self.table:
                    self.table.append([])
                self.cell = []
            return
        if tag in _BLOCK:
            self._flush()
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.heading = int(tag[1])
        elif tag in ('ul', 'ol'):
            self.lists.append([tag, 0])
        elif tag == 'li':
            depth = max(len(self.lists) - 1, 0)
            kind = self.lists[-1] if self.lists else ['ul', 0]
            kind[1] += 1
            bullet = '%d. ' % kind[1] if kind[0] == 'ol' else '- '
            self.li_prefix = '  ' * depth + bullet
        elif tag == 'pre':
            self.pre += 1
            self.line = []
        elif tag == 'blockquote':
            self.quote += 1
        elif tag == 'hr':
            self._emit_raw('p', '---')
        elif tag == 'a':
            self.links.append(attrs.get('href') or '')
            self.line.append('[')
        elif tag in ('strong', 'b'):
            self.line.append('**')
        elif tag in ('em', 'i'):
            self.line.append('*')
        elif tag == 'code' and not self.pre:
            self.line.append('`')

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(self.skip - 1, 0)
            return
        if self.skip:
            return
        if self.cell is not None:
            if tag in ('td', 'th'):
                self._end_cell()
            elif tag == 'a':
                self.cell.append('](%s)' % self._href())
            elif tag in ('strong', 'b'):
                self.cell.append('**')
            elif tag in ('em', 'i'):
                self.cell.append('*')
            elif tag == 'code':
                self.cell.append('`')
            elif tag == 'tr':
                self._end_cell()
            elif tag == 'table':
                self._end_cell()
                self._end_table()
            return
        if self.table is not None:
            if tag == 'table':
                self._end_table()
            return
        if tag == 'pre':
            text = ''.join(self.line).strip('\n')
            self.line = []
            self.pre = max(self.pre - 1, 0)
            self._emit_raw('pre', '```\n%s\n```' % text)
            return
        if tag == 'a':
            self.line.append('](%s)' % self._href())
            return
        if tag in ('strong', 'b'):
            self.line.append('**')
        elif tag in ('em', 'i'):
            self.line.append('*')
        elif tag == 'code' and not self.pre:
            self.line.append('`')
        if tag in _BLOCK:
            self._flush()
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.heading = 0
        elif tag in ('ul', 'ol'):
            if self.lists:
                self.lists.pop()
            if not self.lists:
                self._emit_raw('p', '')     # list ends: blank separator
        elif tag == 'blockquote':
            self.quote = max(self.quote - 1, 0)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ('br', 'hr'):
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.skip:
            return
        if self.cell is not None:
            self.cell.append(data)
        else:
            self.line.append(data)

    # -- tables & links -------------------------------------------------
    def _href(self):
        href = self.links.pop() if self.links else ''
        if href.startswith('/') and self.base_url:
            href = self.base_url + href
        return href

    def _end_cell(self):
        if self.cell is None:
            return
        text = re.sub(r'\s+', ' ', ''.join(self.cell)).strip().replace('|', '\\|')
        if not self.table:
            self.table.append([])
        self.table[-1].append(text)
        self.cell = None

    def _end_table(self):
        rows = [r for r in (self.table or []) if r]
        self.table = None
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [''] * (width - len(r)) for r in rows]
        lines = ['| ' + ' | '.join(rows[0]) + ' |',
                 '|' + '---|' * width]
        lines += ['| ' + ' | '.join(r) + ' |' for r in rows[1:]]
        self._emit_raw('table', '\n'.join(lines))

    def result(self):
        self._flush()
        if self.table is not None:
            self._end_cell()
            self._end_table()
        parts = []
        previous = None
        for kind, text in self.out:
            if kind == 'li' and previous == 'li':
                parts[-1] += '\n' + text
            elif text == '':
                previous = None
                continue
            else:
                parts.append(text)
            previous = kind
        return '\n\n'.join(p for p in parts if p).strip()


def html_to_markdown(body, base_url=''):
    if not body:
        return ''
    writer = _MarkdownWriter(base_url)
    writer.feed(str(body))
    writer.close()
    return writer.result()


# ---------------------------------------------------------------------------
# markdown → HTML (for bodies written back into Knowledge)
# ---------------------------------------------------------------------------

def _inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)',
                  lambda m: '<a href="%s">%s</a>' % (html.escape(m.group(2)), m.group(1)),
                  text)
    return text


def markdown_to_html(text):
    """Convert the markdown this module emits (and plain prose) to HTML."""
    lines = (text or '').replace('\r\n', '\n').split('\n')
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith('```'):
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code.append(lines[i])
                i += 1
            i += 1
            out.append('<pre>%s</pre>' % html.escape('\n'.join(code), quote=False))
            continue
        heading = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if heading:
            level = len(heading.group(1))
            out.append('<h%d>%s</h%d>' % (level, _inline(heading.group(2)), level))
            i += 1
            continue
        if stripped.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                row = lines[i].strip()
                if not re.match(r'^\|[\s:|-]+\|?$', row):
                    cells = re.split(r'(?<!\\)\|', row.strip('|'))
                    rows.append([c.strip().replace('\\|', '|') for c in cells])
                i += 1
            if rows:
                head = ''.join('<th>%s</th>' % _inline(c) for c in rows[0])
                body = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % _inline(c) for c in r)
                               for r in rows[1:])
                out.append('<table class="table table-bordered"><thead><tr>%s</tr></thead>'
                           '<tbody>%s</tbody></table>' % (head, body))
            continue
        if re.match(r'^\s*([-*]|\d+\.)\s+', line):
            out.append(_list_html(lines, i))
            while i < len(lines) and (re.match(r'^\s*([-*]|\d+\.)\s+', lines[i])
                                      or (lines[i].startswith('  ') and lines[i].strip())):
                i += 1
            continue
        if stripped == '---':
            out.append('<hr/>')
            i += 1
            continue
        if stripped.startswith('>'):
            quote = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            out.append('<blockquote><p>%s</p></blockquote>' % _inline(' '.join(quote)))
            continue
        para = []
        while (i < len(lines) and lines[i].strip()
               and not re.match(r'^(#{1,6}\s|\||```|>|\s*([-*]|\d+\.)\s+)', lines[i].strip())):
            para.append(lines[i].strip())
            i += 1
        out.append('<p>%s</p>' % _inline(' '.join(para)))
    return ''.join(out)


def _list_html(lines, start):
    items = []
    i = start
    while i < len(lines):
        match = re.match(r'^(\s*)([-*]|\d+\.)\s+(.*)$', lines[i])
        if not match:
            if lines[i].startswith('  ') and lines[i].strip() and items:
                items[-1] = (items[-1][0], items[-1][1], items[-1][2] + ' ' + lines[i].strip())
                i += 1
                continue
            break
        indent = len(match.group(1).replace('\t', '  ')) // 2
        ordered = match.group(2)[0].isdigit()
        items.append((indent, ordered, match.group(3)))
        i += 1
    html_out = []
    stack = []
    for indent, ordered, text in items:
        while len(stack) > indent + 1:
            html_out.append('</li></%s>' % stack.pop())
        if len(stack) == indent + 1:
            html_out.append('</li>')
        while len(stack) < indent + 1:
            tag = 'ol' if ordered else 'ul'
            stack.append(tag)
            html_out.append('<%s>' % tag)
        html_out.append('<li>%s' % _inline(text))
    while stack:
        html_out.append('</li></%s>' % stack.pop())
    return ''.join(html_out)


# ---------------------------------------------------------------------------
# The workspace: loading, the company filter
# ---------------------------------------------------------------------------

class Workspace:
    """Every article under the root the caller can read, indexed."""

    def __init__(self, backend, include_bodies=False):
        self.backend = backend
        root_id = backend.root_article_id()
        if not root_id:
            raise ContextError(
                'The Odoo context workspace is not configured: set the workspace '
                'root article (in-Odoo module: Settings → MCP Server → Odoo '
                'context; independent server: "context_root_article_id" in '
                'odoo_config.json).')
        self.root_id = int(root_id)
        roots = backend.search_read(MODEL, [('id', '=', self.root_id)],
                                    ['id', 'parent_path', 'name'])
        if not roots:
            raise ContextError(
                'You cannot open the Odoo context workspace (article %d). Ask a '
                'context reviewer to share it with you in Knowledge.' % self.root_id)
        self.root_path = roots[0]['parent_path']
        fields = ARTICLE_FIELDS + (['body'] if include_bodies else [])
        rows = backend.search_read(
            MODEL, [('parent_path', '=like', self.root_path + '%'),
                    ('id', '!=', self.root_id)],
            fields, order='id')
        self.articles = {}
        for row in rows:
            row['props'] = props(row)
            row['has_definition'] = bool(row.get('article_properties'))
            row['parent'] = _m2o_id(row.get('parent_id'))
            self.articles[row['id']] = row
        user = backend.user()
        self.user = user
        self.allowed = set(user.get('company_ids') or [])
        self._allowed_roots = None

    # -- the company filter ----------------------------------------------
    def allowed_roots(self):
        """Root company ids of every company the user is allowed into."""
        if self._allowed_roots is None:
            rows = self.backend.search_read(
                'res.company', [('id', 'in', sorted(self.allowed))],
                ['id', 'root_id'], all_companies=True) if self.allowed else []
            self._allowed_roots = {_m2o_id(r.get('root_id')) or r['id'] for r in rows}
        return self._allowed_roots

    def governor(self, article):
        """The nearest article in the chain (self first) carrying a company id."""
        chain = [article['id']] + list(reversed(_path_ids(article.get('parent_path'))[:-1]))
        for aid in chain:
            node = self.articles.get(aid)
            if node and node['props'].get('ctx_company_id'):
                return node
        return None

    def visible(self, article):
        """The company filter: may this user see this article through the tools?"""
        target = self.articles.get(article['props'].get('ctx_target') or 0)
        if article['props'].get('ctx_type') == 'proposal' and target is not None:
            return self.visible(target)
        gov = self.governor(article)
        if gov is None:
            return True
        company = gov['props']['ctx_company_id']
        if gov['props'].get('ctx_type') == 'structure':
            return company in self.allowed_roots()
        return company in self.allowed

    def approved(self, ctx_type=None):
        for article in self.articles.values():
            p = article['props']
            if p.get('ctx_status') != 'approved' or not p.get('ctx_type'):
                continue
            if ctx_type and p['ctx_type'] != ctx_type:
                continue
            if self.visible(article):
                yield article

    def folder(self, ctx_type):
        for article in self.articles.values():
            if article['props'].get('ctx_type') == ctx_type:
                return article
        return None

    def contains(self, article_id):
        return article_id == self.root_id or article_id in self.articles

    def untyped_count(self):
        """Articles with no properties at all: their parent lacks the definition."""
        return sum(1 for a in self.articles.values()
                   if not a['has_definition'] and self.visible(a))


def _url(backend, article_id):
    base = (backend.base_url() or '').rstrip('/')
    return '%s/knowledge/article/%d' % (base, article_id)


def _article_out(ws, article, body=True):
    p = article['props']
    out = {
        'id': article['id'],
        'title': article.get('name') or '',
        'type': p.get('ctx_type') or None,
        'status': p.get('ctx_status') or None,
        'company_id': p.get('ctx_company_id') or None,
        'structure': p.get('ctx_structure') or None,
        'verified_on': fmt_date(p.get('ctx_verified_on')),
        'verified_via': p.get('ctx_verified_via') or None,
        'url': _url(ws.backend, article['id']),
    }
    if body:
        out['markdown'] = html_to_markdown(article.get('body') or '', ws.backend.base_url())
    return out


def _bodies(ws, articles):
    """Fetch bodies for just these articles (one read)."""
    ids = [a['id'] for a in articles if 'body' not in a]
    if ids:
        rows = ws.backend.search_read(MODEL, [('id', 'in', ids)], ['id', 'body'])
        bodies = {r['id']: r.get('body') or '' for r in rows}
        for a in articles:
            a.setdefault('body', bodies.get(a['id'], ''))
    return articles


# ---------------------------------------------------------------------------
# Live company facts and journal resolution
# ---------------------------------------------------------------------------

COMPANY_FIELDS = ['id', 'name', 'parent_id', 'root_id', 'parent_path', 'currency_id',
                  'fiscalyear_last_day', 'fiscalyear_last_month', 'account_opening_date',
                  'fiscalyear_lock_date', 'tax_lock_date', 'sale_lock_date',
                  'purchase_lock_date', 'hard_lock_date']
JOURNAL_FIELDS = ['id', 'name', 'code', 'type', 'company_id', 'sequence']
SHARED_PROCEDURE = (
    'Shared journal: create the entry under the owning company with this '
    'journal, then set company_id to the target company while the entry is '
    'still in draft. Never for bank journals.')
OWN_PROCEDURE = 'Own journal: post directly.'


def company_facts(backend, company_id):
    rows = backend.search_read('res.company', [('id', '=', company_id)],
                               COMPANY_FIELDS, all_companies=True)
    if not rows:
        raise ContextPolicyError(
            'Company %d does not exist or you are not allowed into it.' % company_id)
    row = rows[0]
    month = int(row.get('fiscalyear_last_month') or 12)
    day = int(row.get('fiscalyear_last_day') or 31)
    currency = _m2o_name(row.get('currency_id'))
    facts = {
        'id': row['id'],
        'name': row.get('name'),
        'parent_id': _m2o_id(row.get('parent_id')),
        'parent': _m2o_name(row.get('parent_id')),
        'root_id': _m2o_id(row.get('root_id')) or row['id'],
        'root': _m2o_name(row.get('root_id')) or row.get('name'),
        'currency': currency,
        'year_end': '%02d %s' % (day, _MONTHS[month - 1]),
        'year_end_ddmm': '%02d-%02d' % (day, month),
        'year_end_month': month,
        'opening_date': fmt_date(row.get('account_opening_date')),
        'lock_dates': {k.replace('_lock_date', ''): fmt_date(row.get(k))
                       for k in ('fiscalyear_lock_date', 'tax_lock_date',
                                 'sale_lock_date', 'purchase_lock_date',
                                 'hard_lock_date') if row.get(k)},
        'ancestor_ids': _path_ids(row.get('parent_path'))[:-1],
    }
    return facts


def _journal_out(journal, company_id, shared):
    return {
        'id': journal['id'],
        'code': journal.get('code'),
        'name': journal.get('name'),
        'company_id': _m2o_id(journal.get('company_id')),
        'company': _m2o_name(journal.get('company_id')),
        'shared': shared,
        'procedure': SHARED_PROCEDURE if shared else OWN_PROCEDURE,
    }


def resolve_journals(backend, facts, allowed):
    """The ``journals`` block: own journals first, else the nearest ancestor's.

    Bank journals are only ever the company's own. The consolidation journal
    (code CJE) is only looked for in the company and its ancestors, nearest
    first, because CJE exists in several unrelated structures.
    """
    company_id = facts['id']
    chain = [company_id] + list(reversed(facts['ancestor_ids']))   # nearest first
    visible_chain = [c for c in chain if c in allowed]
    hidden = [c for c in chain if c not in allowed]
    rows = backend.search_read(
        'account.journal', [('company_id', 'in', visible_chain), ('active', '=', True)],
        JOURNAL_FIELDS, order='sequence, id', all_companies=True) if visible_chain else []
    by_company = {}
    for row in rows:
        by_company.setdefault(_m2o_id(row.get('company_id')), []).append(row)

    def hidden_note(kind):
        if hidden:
            return ('No %s journal in this company or the ancestors you can access. '
                    'Ancestor companies %s are outside your allowed companies; if the '
                    'shared journal is there, ask an administrator for access.'
                    % (kind, ', '.join(str(c) for c in hidden)))
        return 'No %s journal in this company or any ancestor.' % kind

    out = {}
    # Bank: own company only, every active bank journal.
    own_bank = [j for j in by_company.get(company_id, []) if j.get('type') == 'bank']
    if own_bank:
        out['bank'] = [_journal_out(j, company_id, False) for j in own_bank]
    else:
        out['bank'] = {'error': 'Company %d has no bank journal of its own. Bank '
                                'journals are never resolved from another company.'
                                % company_id}

    for kind in ('general', 'purchase', 'sale'):
        found = None
        for owner in chain:
            candidates = [j for j in by_company.get(owner, []) if j.get('type') == kind]
            if kind == 'general':
                # The day-to-day miscellaneous journal: not consolidation or FX.
                candidates = [j for j in candidates
                              if (j.get('code') or '').upper() not in ('CJE', 'EXCH')]
            if candidates:
                found = (owner, candidates)
                break
        if not found:
            out[kind] = {'error': hidden_note(kind)}
            continue
        owner, candidates = found
        entry = _journal_out(candidates[0], company_id, owner != company_id)
        if len(candidates) > 1:
            entry['alternatives'] = [{'id': j['id'], 'code': j.get('code'),
                                      'name': j.get('name')} for j in candidates[1:]]
        out[kind] = entry

    for owner in chain:
        cje = [j for j in by_company.get(owner, [])
               if (j.get('code') or '').upper() == 'CJE' and j.get('type') == 'general']
        if cje:
            out['consolidation'] = _journal_out(cje[0], company_id, owner != company_id)
            break
    else:
        if hidden:
            out['consolidation'] = {'error': hidden_note('consolidation (CJE)')}
    return out


# ---------------------------------------------------------------------------
# Tool: odoo_context_for
# ---------------------------------------------------------------------------

def context_for(backend, args):
    company_id = _int(args.get('company_id'), 'company_id')
    task = args.get('task') or None
    if task and task not in TASK_TYPES:
        raise ContextError('task must be one of %s, got %r.'
                           % (', '.join(sorted(TASK_TYPES)), task))
    user = backend.user()
    allowed = set(user.get('company_ids') or [])
    if company_id not in allowed:
        raise ContextPolicyError(
            'Refusing company %d — it is not one of your allowed companies in Odoo.'
            % company_id)
    facts = company_facts(backend, company_id)
    warnings = []
    if facts['year_end_month'] != 12:
        warnings.append('Year end %s: a %s close is a year-end close.'
                        % (facts['year_end'], _MONTH_NAMES[facts['year_end_month'] - 1]))
    result = {
        'company': {k: v for k, v in facts.items()
                    if k not in ('year_end_ddmm', 'year_end_month', 'ancestor_ids')},
        'journals': resolve_journals(backend, facts, allowed),
        'structure': None,
        'articles': [],
        'warnings': warnings,
    }
    try:
        ws = Workspace(backend)
    except ContextError as error:
        warnings.append('No articles: %s' % error)
        return result

    wanted = TASK_TYPES.get(task, ALL_ROUTED)
    routed = []

    def pick(ctx_type, label, company=None, parent=None):
        matches = [a for a in ws.approved(ctx_type)
                   if (company is None or a['props'].get('ctx_company_id') == company)
                   and (parent is None or a['parent'] == parent)]
        if not matches:
            warnings.append('No approved %s article found.' % label)
            return None
        if len(matches) > 1:
            warnings.append('%d approved %s articles found; using the oldest (id %d).'
                            % (len(matches), label, matches[0]['id']))
        return matches[0]

    if 'guide' in wanted:
        guide = pick('guide', '"Read me first" guide')
        if guide:
            routed.append(guide)
    if 'entity' in wanted:
        entity = pick('entity', 'entity', company=company_id)
        if entity:
            routed.append(entity)
            warnings.extend(_drift(entity, facts))
    if 'rulebook' in wanted:
        rulebook = pick('rulebook', 'rulebook', company=company_id)
        if rulebook:
            routed.append(rulebook)
    structure = pick('structure', 'structure (for root company %d)' % facts['root_id'],
                     company=facts['root_id'])
    if structure:
        result['structure'] = structure['props'].get('ctx_structure') or structure.get('name')
        for part in STRUCTURE_PARTS:
            if part in wanted:
                article = pick(part, '%s %s' % (result['structure'], part),
                               parent=structure['id'])
                if article:
                    routed.append(article)

    stale = backend.stale_days()
    today = backend.today()
    for article in routed:
        verified = _parse_date(article['props'].get('ctx_verified_on'))
        if not verified:
            warnings.append('"%s" has no verified-on date.' % article.get('name'))
        elif (today - verified).days > stale:
            warnings.append('"%s" is stale: last verified %s (more than %d days ago).'
                            % (article.get('name'), fmt_date(verified), stale))
    untyped = ws.untyped_count()
    if untyped:
        warnings.append('%d workspace article(s) have no context properties, so the '
                        'tools ignore them: their parent article lacks the property '
                        'definition.' % untyped)
    _bodies(ws, routed)
    result['articles'] = [_article_out(ws, a) for a in routed]
    return result


def _drift(entity, facts):
    """Compare live company facts with the entity article. Never edits it."""
    p = entity['props']
    out = []
    checks = (
        ('ctx_year_end', 'year end', facts['year_end_ddmm']),
        ('ctx_parent_id', 'parent company id', facts['parent_id'] or False),
        ('ctx_currency', 'currency', facts['currency']),
    )
    for name, label, live in checks:
        recorded = p.get(name)
        if name == 'ctx_parent_id' and recorded in (0, False, None, ''):
            # An integer property cannot be blank, so "no parent" is stored as 0
            # and may read back as 0 or False — indistinguishable from "never
            # filled". Treat it as "no parent": fine for a top-level company,
            # drift if Odoo now shows a parent.
            if live:
                out.append('Drift: parent company id is %s in Odoo but none in the entity '
                           'article "%s". Propose a correction to the article.'
                           % (live, entity.get('name')))
            continue
        if recorded in (False, None, ''):
            out.append('Drift check: the entity article has no %s (%s) to compare.'
                       % (label, name))
            continue
        if str(recorded).strip().upper() != str(live).strip().upper():
            out.append('Drift: %s is %s in Odoo but %s in the entity article "%s". '
                       'Propose a correction to the article.'
                       % (label, live, recorded, entity.get('name')))
    return out


# ---------------------------------------------------------------------------
# Tool: odoo_context_lookup
# ---------------------------------------------------------------------------

def lookup(backend, args):
    query = (args.get('query') or '').strip()
    article_id = args.get('article_id')
    if bool(query) == (article_id not in (None, '', False)):
        raise ContextError('Pass exactly one of query or article_id.')
    if article_id not in (None, '', False):
        return _lookup_one(backend, _int(article_id, 'article_id'))
    return _search(backend, query, args)


def _lookup_one(backend, article_id):
    ws = Workspace(backend)
    article = ws.articles.get(article_id)
    if article is None or not ws.visible(article):
        raise ContextError('Article %d not found in the Odoo context workspace.' % article_id)
    p = article['props']
    if not p.get('ctx_type'):
        raise ContextError('Article %d not found in the Odoo context workspace.' % article_id)
    if p['ctx_type'] != 'proposal' and p.get('ctx_status') != 'approved':
        raise ContextError('Article %d not found in the Odoo context workspace.' % article_id)
    _bodies(ws, [article])
    out = _article_out(ws, article)
    out['properties'] = {k: (fmt_date(v) if k == 'ctx_verified_on' else v)
                         for k, v in p.items() if v not in (False, None)}
    return out


def _search(backend, query, args):
    limit = args.get('limit') or 10
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        raise ContextError('limit must be an integer.')
    structure = args.get('structure') or None
    ctx_type = args.get('ctx_type') or None
    if ctx_type and ctx_type not in TYPES:
        raise ContextError('ctx_type must be one of %s.' % ', '.join(TYPES))
    include_proposals = bool(args.get('include_proposals'))
    ws = Workspace(backend)
    rows = backend.search_read(
        MODEL, [('parent_path', '=like', ws.root_path + '%'), ('id', '!=', ws.root_id),
                '|', ('name', 'ilike', query), ('body', 'ilike', query)],
        ['id', 'name', 'body'], order='id', limit=500)
    needle = query.lower()
    hits = []
    for row in rows:
        article = ws.articles.get(row['id'])
        if article is None or not ws.visible(article):
            continue
        p = article['props']
        kind = p.get('ctx_type')
        if not kind:
            continue
        if kind in ('proposal', 'proposals', 'rejected'):
            if not include_proposals or kind != 'proposal':
                continue
        elif p.get('ctx_status') != 'approved':
            continue
        if ctx_type and kind != ctx_type:
            continue
        if structure and (p.get('ctx_structure') or '').lower() != structure.lower() \
                and not _under_structure(ws, article, structure):
            continue
        text = html_to_markdown(row.get('body') or '')
        title = (row.get('name') or '')
        score = (10 if needle in title.lower() else 0) + text.lower().count(needle)
        hits.append((-score, article['id'], article, text))
    hits.sort(key=lambda h: (h[0], h[1]))
    results = []
    for _score, _id, article, text in hits[:limit]:
        out = _article_out(ws, article, body=False)
        out['snippet'] = _snippet(text, needle)
        results.append(out)
    return {'query': query, 'count': len(results), 'results': results}


def _under_structure(ws, article, structure):
    for aid in _path_ids(article.get('parent_path')):
        node = ws.articles.get(aid)
        if node and node['props'].get('ctx_type') == 'structure' and \
                (node['props'].get('ctx_structure') or '').lower() == structure.lower():
            return True
    return False


def _snippet(text, needle, width=160):
    flat = re.sub(r'\s+', ' ', text)
    at = flat.lower().find(needle)
    if at < 0:
        return flat[:width] + ('…' if len(flat) > width else '')
    start = max(at - width // 3, 0)
    piece = flat[start:start + width]
    return ('…' if start else '') + piece + ('…' if start + width < len(flat) else '')


# ---------------------------------------------------------------------------
# Tool: odoo_context_propose
# ---------------------------------------------------------------------------

# The machine copies are marked by class, not data-* attributes: Knowledge's
# HTML sanitiser keeps classes but may strip unknown attributes.
_DATA_RE = re.compile(r'<pre[^>]*class="o_ctx_(change|new)"[^>]*>(.*?)</pre>', re.S)


def propose(backend, args):
    if not backend.has_group(CONTRIBUTOR_GROUP):
        raise ContextPolicyError(
            'Refusing to file a proposal: you are not in the "Context contributor" group.')
    change = args.get('change')
    if not isinstance(change, str) or not change.strip():
        raise ContextError('change is required: the complete new article body in markdown.')
    evidence = (args.get('evidence') or '').strip()
    reason = (args.get('reason') or '').strip()
    if not evidence:
        raise ContextError('evidence is required: the Odoo calls or documents that support the change.')
    if not reason:
        raise ContextError('reason is required.')
    target_id = args.get('target_article_id')
    new = args.get('new')
    if bool(target_id) == bool(new):
        raise ContextError('Pass exactly one of target_article_id or new.')
    ws = Workspace(backend)
    inbox = ws.folder('proposals')
    if inbox is None:
        raise ContextError('The workspace has no Proposals folder (an article with '
                           'ctx_type = proposals). Ask a context reviewer to add one.')
    new_spec = None
    if target_id:
        target_id = _int(target_id, 'target_article_id')
        target = ws.articles.get(target_id)
        if target is None:
            raise ContextPolicyError(
                'Refusing: article %d is not in the Odoo context workspace.' % target_id)
        if not ws.visible(target):
            raise ContextPolicyError(
                'Refusing: article %d belongs to a company outside your allowed '
                'companies.' % target_id)
        tp = target['props']
        if not tp.get('ctx_type') or tp['ctx_type'] in NOT_PROPOSABLE:
            raise ContextPolicyError(
                'Refusing: article %d is not a context article that can be changed '
                'by proposal.' % target_id)
        if tp.get('ctx_status') != 'approved':
            raise ContextPolicyError('Refusing: article %d is not approved.' % target_id)
        title = 'Proposal: %s' % (target.get('name') or target_id)
        company = tp.get('ctx_company_id') or False
        struct = tp.get('ctx_structure') or False
    else:
        if not isinstance(new, dict):
            raise ContextError('new must be an object: {parent_id, title, properties}.')
        parent_id = _int(new.get('parent_id'), 'new.parent_id')
        new_title = (new.get('title') or '').strip()
        if not new_title:
            raise ContextError('new.title is required.')
        parent = ws.articles.get(parent_id)
        if parent_id != ws.root_id and parent is None:
            raise ContextPolicyError(
                'Refusing: parent %d is not in the Odoo context workspace.' % parent_id)
        if parent is not None:
            if not ws.visible(parent):
                raise ContextPolicyError(
                    'Refusing: parent %d belongs to a company outside your allowed '
                    'companies.' % parent_id)
            if parent['props'].get('ctx_type') in ('proposal', 'proposals', 'rejected', 'log'):
                raise ContextPolicyError('Refusing: articles cannot be added under %s.'
                                         % parent.get('name'))
        new_props = dict(new.get('properties') or {})
        unknown = set(new_props) - set(PROPERTY_NAMES)
        if unknown:
            raise ContextError('Unknown properties: %s.' % ', '.join(sorted(unknown)))
        kind = new_props.get('ctx_type')
        if kind not in TYPES or kind in NOT_PROPOSABLE:
            raise ContextError('new.properties.ctx_type must be one of %s.'
                               % ', '.join(t for t in TYPES if t not in NOT_PROPOSABLE))
        for key in ('ctx_status', 'ctx_verified_on', 'ctx_verified_via', 'ctx_target'):
            new_props.pop(key, None)
        company = new_props.get('ctx_company_id') or False
        if company and int(company) not in ws.allowed:
            raise ContextPolicyError(
                'Refusing: company %s is outside your allowed companies.' % company)
        struct = new_props.get('ctx_structure') or False
        new_spec = {'parent_id': parent_id, 'title': new_title, 'properties': new_props}
        title = 'Proposal (new): %s' % new_title
    user = backend.user()
    body = ''.join([
        '<h2>Proposed change</h2>',
        '<p>Proposed by %s on %s.</p>' % (html.escape(user.get('name') or ''),
                                          fmt_date(backend.today())),
        '<h3>Reason</h3><p>%s</p>' % html.escape(reason),
        '<h3>Evidence</h3><p>%s</p>' % html.escape(evidence),
        '<h3>New text</h3>',
        markdown_to_html(change),
        '<h3>Machine copy (applied on approval — edit here to amend)</h3>',
        '<pre class="o_ctx_change">%s</pre>' % html.escape(change, quote=False),
    ])
    if new_spec:
        body += '<pre class="o_ctx_new">%s</pre>' % html.escape(
            json.dumps(new_spec, sort_keys=True), quote=False)
    values = {
        'name': title,
        'parent_id': inbox['id'],
        'body': body,
        'article_properties': {
            'ctx_type': 'proposal',
            'ctx_status': 'proposed',
            'ctx_target': target_id or False,
            'ctx_company_id': company,
            'ctx_structure': struct,
            'ctx_verified_via': evidence[:250],
        },
    }
    proposal_id = backend.create(MODEL, values, elevated=True)
    notified = True
    try:
        backend.post_message(
            proposal_id,
            '<p>New context proposal for review: %s</p>' % html.escape(title),
            backend.group_partner_ids(REVIEWER_GROUP))
    except Exception:          # notification is best effort; the proposal stands
        notified = False
    out = {'proposal_id': proposal_id, 'url': _url(backend, proposal_id),
           'status': 'proposed', 'target_article_id': target_id or None}
    if not notified:
        out['warning'] = 'The proposal was filed but reviewers could not be notified.'
    return out


# ---------------------------------------------------------------------------
# Tool: odoo_context_apply_proposal
# ---------------------------------------------------------------------------

def apply_proposal(backend, args):
    if not backend.has_group(REVIEWER_GROUP):
        raise ContextPolicyError(
            'Refusing: only the "Context reviewer" group can apply or reject proposals.')
    proposal_id = _int(args.get('proposal_id'), 'proposal_id')
    decision = args.get('decision')
    if decision not in ('approve', 'reject'):
        raise ContextError("decision must be 'approve' or 'reject'.")
    note = (args.get('note') or '').strip()
    ws = Workspace(backend, include_bodies=True)
    proposal = ws.articles.get(proposal_id)
    if proposal is None or proposal['props'].get('ctx_type') != 'proposal':
        raise ContextPolicyError(
            'Refusing: article %d is not a proposal in the Odoo context workspace.'
            % proposal_id)
    pp = proposal['props']
    if pp.get('ctx_status') != 'proposed':
        raise ContextPolicyError('Refusing: proposal %d is already %s.'
                                 % (proposal_id, pp.get('ctx_status') or 'closed'))
    if not ws.visible(proposal):
        raise ContextPolicyError(
            'Refusing: proposal %d concerns a company outside your allowed companies.'
            % proposal_id)
    user = backend.user()
    today = backend.today()
    if decision == 'reject':
        values = {'article_properties': merged_properties(proposal, {'ctx_status': 'rejected'})}
        archive = ws.folder('rejected')
        if archive is not None:
            values['parent_id'] = archive['id']
        backend.write(MODEL, [proposal_id], values)
        _post_quietly(backend, proposal_id, '<p>Rejected by %s on %s. %s</p>'
                      % (html.escape(user.get('name') or ''), fmt_date(today),
                         html.escape(note)))
        return {'proposal_id': proposal_id, 'status': 'rejected',
                'filed_under': archive['id'] if archive else None}

    found = {kind: html.unescape(text)
             for kind, text in _DATA_RE.findall(str(proposal.get('body') or ''))}
    if 'change' not in found:
        raise ContextError('Proposal %d has no machine copy of the change to apply.' % proposal_id)
    change_html = markdown_to_html(found['change'])
    evidence = pp.get('ctx_verified_via') or ''
    verified = {'ctx_verified_on': today.isoformat(), 'ctx_verified_via': evidence[:250],
                'ctx_status': 'approved'}
    target_id = pp.get('ctx_target')
    if target_id:
        target = ws.articles.get(target_id)
        if target is None or target['props'].get('ctx_status') != 'approved':
            raise ContextError('The target article %s no longer exists or is not approved.'
                               % target_id)
        if not ws.visible(target):
            raise ContextPolicyError(
                'Refusing: the target belongs to a company outside your allowed companies.')
        backend.write(MODEL, [target_id], {'body': change_html,
                                          'article_properties': merged_properties(target, verified)})
        article_name = target.get('name')
        article_id = target_id
    else:
        if 'new' not in found:
            raise ContextError('Proposal %d has neither a target nor a new-article spec.'
                               % proposal_id)
        spec = json.loads(found['new'])
        parent_id = int(spec['parent_id'])
        if not ws.contains(parent_id):
            raise ContextPolicyError('Refusing: the new article\'s parent is outside the workspace.')
        properties = dict(spec.get('properties') or {})
        properties.update(verified)
        article_id = backend.create(MODEL, {
            'name': spec['title'], 'parent_id': parent_id, 'body': change_html,
            'article_properties': properties})
        article_name = spec['title']
    proposer = _m2o_name(proposal.get('create_uid')) or ''
    log_warning = _append_log(ws, backend, today, article_id, article_name, proposal,
                              proposer, user.get('name') or '', note)
    backend.write(MODEL, [proposal_id], {
        'article_properties': merged_properties(proposal, {'ctx_status': 'approved'})})
    _post_quietly(backend, proposal_id, '<p>Approved by %s on %s. %s</p>'
                  % (html.escape(user.get('name') or ''), fmt_date(today), html.escape(note)))
    out = {'proposal_id': proposal_id, 'status': 'approved', 'article_id': article_id,
           'url': _url(backend, article_id), 'verified_on': fmt_date(today)}
    if log_warning:
        out['warning'] = log_warning
    return out


def _post_quietly(backend, article_id, body):
    try:
        backend.post_message(article_id, body)
    except Exception:
        pass


def _append_log(ws, backend, today, article_id, article_name, proposal, proposer,
                approver, note):
    log = ws.folder('log')
    if log is None:
        return ('The change was applied, but the workspace has no Change log article '
                '(ctx_type = log), so no row was added.')
    summary = proposal.get('name') or ''
    if note:
        summary += ' (%s)' % note
    row = ('<tr><td>%s</td><td><a href="/knowledge/article/%d">%s</a></td><td>%s</td>'
           '<td>%s</td><td>%s</td></tr>' % (
               fmt_date(today), article_id, html.escape(article_name or ''),
               html.escape(summary), html.escape(proposer), html.escape(approver)))
    # str(): Odoo returns Html fields as Markup, and Markup + str would escape
    # the new row into text.
    body = str(log.get('body') or '')
    marker = body.rfind('</tbody>')
    if marker < 0:
        marker = body.rfind('</table>')
    if marker >= 0:
        body = body[:marker] + row + body[marker:]
    else:
        body += ('<table class="table table-bordered"><thead><tr><th>Date</th>'
                 '<th>Article</th><th>Change</th><th>Proposed by</th>'
                 '<th>Approved by</th></tr></thead><tbody>%s</tbody></table>' % row)
    backend.write(MODEL, [log['id']], {'body': body})
    return None


# ---------------------------------------------------------------------------
# The workspace write guard (generic tools on both servers)
# ---------------------------------------------------------------------------

def merged_properties(article, updates):
    """Every ``ctx_*`` value the article has, with ``updates`` applied.

    Properties are written whole, so a partial dict can never reset a value
    the update did not mention.
    """
    values = {k: v for k, v in (article.get('props') or props(article)).items()}
    values.update(updates)
    return values


def _ints(value, out):
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        out.add(value)
    elif isinstance(value, str) and value.strip().isdigit():
        out.add(int(value))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _ints(item, out)
    elif isinstance(value, dict):
        for item in value.values():
            _ints(item, out)


GUARD_MESSAGE = (
    'Refusing to change the Odoo context workspace through the generic tools — '
    'workspace articles change only through odoo_context_propose and '
    'odoo_context_apply_proposal, so every change is reviewed and logged.')


def in_workspace(parent_path, root_id):
    """Is an article with this parent_path the root or under it?"""
    return bool(root_id) and int(root_id) in _path_ids(parent_path)


def guard_records(model, ids, values, root_id_of, paths_of):
    """The guard for create / write / archive / cancel / unlink.

    Checks the records being changed and, for a create or write, the
    ``parent_id`` (articles) or ``article_id`` (members) the values point at.
    """
    if model not in GUARDED_MODELS:
        return
    article_ids, member_ids = set(), set()
    target = article_ids if model == MODEL else member_ids
    _ints(ids or [], target)
    for values_one in (values if isinstance(values, list) else [values or {}]):
        if isinstance(values_one, dict):
            _ints(values_one.get('parent_id'), article_ids)
            _ints(values_one.get('article_id'), article_ids)
    _check(article_ids, member_ids, root_id_of, paths_of)


def guard_execute(model, method, args, kwargs, root_id_of, paths_of):
    """The guard for odoo_execute: any non-read method on the two models.

    Every integer in the call's arguments is treated as a possible article (or
    member) id — record ids, a parent passed to ``article_create``, ids inside
    values. That over-approximates on purpose: refusing a stray call on these
    two models is cheap; missing a write to an approved article is not.
    """
    if model not in GUARDED_MODELS or method in READ_METHODS:
        return
    found = set()
    _ints(args, found)
    _ints(kwargs, found)
    _check(found, found if model != MODEL else set(), root_id_of, paths_of)


def _check(article_ids, member_ids, root_id_of, paths_of):
    """``root_id_of()`` is the configured root id or None (guard off).
    ``paths_of(model, ids)`` returns ``{id: parent_path}`` — for members, the
    parent_path of the member's article."""
    if not article_ids and not member_ids:
        return
    root_id = root_id_of()
    if not root_id:
        return
    paths = {}
    if article_ids:
        paths.update(paths_of(MODEL, sorted(article_ids)))
    if member_ids:
        paths.update({('member', k): v for k, v in
                      paths_of('knowledge.article.member', sorted(member_ids)).items()})
    for path in paths.values():
        if in_workspace(path, root_id):
            raise ContextPolicyError(GUARD_MESSAGE)
