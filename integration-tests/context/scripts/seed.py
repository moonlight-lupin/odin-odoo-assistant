# Seed the throwaway database (run inside `odoo shell`). Mirrors production's
# shape: the shc-ii and sp6 company chains and their journals, finance users
# with different company access, and the Knowledge workspace from the spec.
# Prints one JSON line with the ids and API keys the runner needs.
import datetime
import json

from odoo import fields

env = self.env  # noqa: F821  (odoo shell)
Company = env['res.company']
admin = env.ref('base.user_admin')


def company(name, parent=None, month='12'):
    vals = {'name': name, 'fiscalyear_last_month': month, 'fiscalyear_last_day': 31,
            'currency_id': env.ref('base.GBP').id}
    if parent:
        vals['parent_id'] = parent.id
    rec = Company.create(vals)
    return rec


env.ref('base.GBP').active = True
c = {}
c['6'] = company('STRAITS HOLDCO II LIMITED', month='7')
c['7'] = company('QIP BERESFORD LIMITED', c['6'], '7')
c['9'] = company('B WOOLWICH LIMITED', c['7'], '7')
c['67'] = company('Woolwich 1 Elimination', c['9'], '7')
c['8'] = company('QIP B WOOLWICH 2 LIMITED', c['7'], '7')
c['68'] = company('Woolwich 2 Elimination', c['8'], '7')
c['14'] = company('Q.I.P. SP6 UK HOLDCO LIMITED')
c['17'] = company('EDINBURGH (LONDON ROAD) SPV LIMITED', c['14'])
c['23'] = company('Edinburgh 1 Elimination', c['17'])
c['61'] = company('QIP DEVELOPMENT GROUP LLC')
c['67'].account_opening_date = '2025-08-01'

# Journals as on production. Bank journals need a default account; give each
# owning company a plain asset account for it.
J = {}


def journal(key, code, jtype, comp, seq=10):
    vals = {'name': code, 'code': code, 'type': jtype, 'company_id': comp.id, 'sequence': seq}
    if jtype == 'bank':
        account = env['account.account'].create({
            'name': 'Bank %s' % code, 'code': '1010%s' % len(J),
            'account_type': 'asset_cash', 'company_ids': [(6, 0, [comp.id])]})
        vals['default_account_id'] = account.id
    J[key] = env['account.journal'].with_company(comp).create(vals)


journal('BILL6', 'BILL', 'purchase', c['6'])
journal('INV6', 'INV', 'sale', c['6'])
journal('MISC6', 'MISC', 'general', c['6'], 5)
journal('BNK1', 'BNK1', 'bank', c['6'])
journal('CJE6', 'CJE', 'general', c['6'], 1)
journal('EXCH6', 'EXCH', 'general', c['6'], 2)
journal('BNK2', 'BNK2', 'bank', c['7'])
journal('BNK4', 'BNK4', 'bank', c['9'])
journal('BNK7', 'BNK7', 'bank', c['9'])
journal('CJE14', 'CJE', 'general', c['14'])
journal('MISC14', 'MISC', 'general', c['14'])
journal('BILL14', 'BILL', 'purchase', c['14'])
journal('CJE61', 'CJE', 'general', c['61'])

shc = [c[k].id for k in ('6', '7', '9', '67', '8', '68')]
sp6 = [c[k].id for k in ('14', '17', '23')]
contributor = env.ref('odoo_module_mcp_server.group_context_contributor')
reviewer = env.ref('odoo_module_mcp_server.group_context_reviewer')
billing = env.ref('account.group_account_invoice')
everyone = [x.id for x in c.values()] + [env.ref('base.main_company').id]


def user(login, companies, groups):
    return env['res.users'].create({
        'name': login.title(), 'login': login, 'password': login + '-pw-123',
        'tz': 'Asia/Singapore', 'company_id': companies[0],
        'company_ids': [(6, 0, companies)],
        'groups_id': [(6, 0, [env.ref('base.group_user').id, billing.id] + groups)]})


admin.write({'company_ids': [(4, i) for i in everyone], 'tz': 'Asia/Singapore',
             'groups_id': [(4, reviewer.id), (4, billing.id)]})
users = {
    'admin': admin,
    'contrib': user('contrib', shc, [contributor.id]),
    'sp6': user('sp6', sp6, [contributor.id]),
    'w1only': user('w1only', [c['67'].id], [contributor.id]),
    'outsider': user('outsider', everyone, [contributor.id]),
    'plain': user('plain', shc, []),
    'reviewer2': user('reviewer2', shc, [reviewer.id]),
}

# ---- the workspace ---------------------------------------------------------
from odoo.addons.odoo_module_mcp_server import context_core  # noqa: E402

A = env['knowledge.article']
DEF = context_core.PROPERTY_DEFINITION
finance = [u for k, u in users.items() if k != 'outsider']
members = [(0, 0, {'partner_id': u.partner_id.id,
                   'permission': 'write' if u.has_group(
                       'odoo_module_mcp_server.group_context_reviewer') else 'read'})
           for u in finance]
ok = {'ctx_status': 'approved', 'ctx_verified_on': '2026-10-01',
      'ctx_verified_via': 'odoo_read(res.company)'}


def art(name, parent=None, defines=False, body=None, **props):
    vals = {'name': name, 'body': body or '<p>%s body</p>' % name}
    if parent:
        vals['parent_id'] = parent.id
    else:
        vals.update({'internal_permission': 'none', 'article_member_ids': members})
    if defines:
        vals['article_properties_definition'] = DEF
    rec = A.create(vals)
    if props:
        rec.article_properties = props
    return rec


root = art('Finance – Odoo context', defines=True)
guide = art('Read me first', root, body='<h1>Read me first</h1><p>Five things that bite.</p>',
            ctx_type='guide', **ok)
structures = art('Structures', root, defines=True)
shcii = art('shc-ii', structures, defines=True, ctx_type='structure',
            ctx_company_id=c['6'].id, ctx_structure='shc-ii', **ok)
parts = {}
for kind, label in (('journals', 'Journals'), ('chart', 'Chart of accounts'),
                    ('taxes', 'Taxes'), ('banking', 'Banking'), ('partners', 'Partners')):
    parts[kind] = art(label, shcii, body='<h2>%s</h2><p>shc-ii %s. Use MISC.</p>' % (label, kind),
                      ctx_type=kind, ctx_structure='shc-ii', **ok)
sp6s = art('sp6', structures, defines=True, ctx_type='structure', ctx_company_id=c['14'].id,
           ctx_structure='sp6', **ok)
sp6j = art('Journals', sp6s, ctx_type='journals', ctx_structure='sp6', **ok)
sp6c = art('Chart of accounts', sp6s, ctx_type='chart', ctx_structure='sp6', **ok)
entities = art('Entities', root, defines=True)
w1 = art('Woolwich 1 Elimination', entities,
         body='<h1>Woolwich 1 Elimination</h1><p>Elimination shell for B Woolwich.</p>'
              '<table><tr><th>Field</th><th>Value</th></tr><tr><td>Year end</td><td>31 Jul</td></tr></table>',
         ctx_type='entity', ctx_company_id=c['67'].id, ctx_year_end='31-07',
         ctx_parent_id=c['9'].id, ctx_currency='GBP', **ok)
ed1 = art('Edinburgh 1 Elimination', entities, ctx_type='entity', ctx_company_id=c['23'].id,
          ctx_year_end='31-12', ctx_parent_id=c['17'].id, ctx_currency='GBP', **ok)
bw = art('B Woolwich', entities, ctx_type='entity', ctx_company_id=c['9'].id,
         ctx_year_end='31-07', ctx_parent_id=c['7'].id, ctx_currency='GBP', **ok)
rules = art('Posting rules', root, defines=True)
w1r = art('Woolwich 1 Elimination rulebook', rules, ctx_type='rulebook',
          ctx_company_id=c['67'].id, **ok)
ed1r = art('Edinburgh 1 Elimination rulebook', rules, ctx_type='rulebook',
           ctx_company_id=c['23'].id, **ok)
log = art('Change log', root, ctx_type='log', ctx_status='approved',
          body='<table><thead><tr><th>Date</th><th>Article</th><th>Change</th>'
               '<th>Proposed by</th><th>Approved by</th></tr></thead><tbody></tbody></table>')
proposals = art('Proposals', root, defines=True, ctx_type='proposals', ctx_status='approved')
rejected = art('Rejected', proposals, defines=True, ctx_type='rejected', ctx_status='approved')
stray = art('Stray note', parts['journals'])          # parent has no definition
outside = A.create({'name': 'Unrelated', 'internal_permission': 'write'})

params = env['ir.config_parameter'].sudo()
params.set_param('odoo_module_mcp_server.context_root_article_id', str(root.id))
params.set_param('web.base.url', __import__('os').environ.get('CTX_BASE_URL', 'http://localhost:18069'))
params.set_param('web.base.url.freeze', 'True')

keys = {}
for name, u in users.items():
    keys[name] = env['res.users.apikeys'].with_user(u)._generate('rpc', 'ctx-test', fields.Datetime.now() + datetime.timedelta(days=30))

env.cr.commit()
print('SEED' + json.dumps({
    'companies': {k: v.id for k, v in c.items()},
    'journals': {k: v.id for k, v in J.items()},
    'users': {k: v.id for k, v in users.items()},
    'logins': {k: v.login for k, v in users.items()},
    'keys': keys,
    'articles': {'root': root.id, 'guide': guide.id, 'shcii': shcii.id,
                 **{k: v.id for k, v in parts.items()}, 'sp6': sp6s.id, 'sp6j': sp6j.id,
                 'sp6c': sp6c.id, 'entities': entities.id, 'w1': w1.id, 'ed1': ed1.id,
                 'bw': bw.id, 'w1r': w1r.id, 'ed1r': ed1r.id, 'log': log.id,
                 'proposals': proposals.id, 'rejected': rejected.id, 'stray': stray.id,
                 'outside': outside.id},
}))
