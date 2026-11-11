# TEST STAND-IN ONLY — never deploy. Odoo's Knowledge app is Enterprise and
# cannot run in this harness, so this addon reproduces the parts of
# knowledge.article the context tools touch: the tree (parent_path), the body,
# properties defined on the parent (Odoo's real fields.Properties), members and
# per-article permissions enforced with record rules, chatter, and archive.
{
    'name': 'Knowledge (test stand-in)',
    'version': '18.0.0.0.1',
    'depends': ['base', 'mail'],
    'data': ['security/ir.model.access.csv', 'security/rules.xml'],
    'installable': True,
    'license': 'LGPL-3',
}
