from odoo import api, fields, models


class KnowledgeArticle(models.Model):
    _name = 'knowledge.article'
    _description = 'Knowledge article (test stand-in)'
    _inherit = ['mail.thread']
    _parent_store = True
    _order = 'sequence, id'

    name = fields.Char()
    body = fields.Html()  # Odoo's default sanitiser, as Knowledge's body
    sequence = fields.Integer()
    active = fields.Boolean(default=True)
    parent_id = fields.Many2one('knowledge.article', index=True, ondelete='cascade')
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many('knowledge.article', 'parent_id')
    internal_permission = fields.Selection(
        [('write', 'Can edit'), ('read', 'Can read'), ('none', 'No access')], default='write')
    article_member_ids = fields.One2many('knowledge.article.member', 'article_id')
    article_properties_definition = fields.PropertiesDefinition('Article Item Properties')
    article_properties = fields.Properties(
        'Properties', definition='parent_id.article_properties_definition', copy=True)
    article_url = fields.Char(compute='_compute_article_url')

    def _compute_article_url(self):
        for article in self:
            article.article_url = '/knowledge/article/%s' % article.id

    @api.model_create_multi
    def create(self, vals_list):
        # Like real Knowledge: a child inherits its parent's permission and members.
        for vals in vals_list:
            parent = self.browse(vals.get('parent_id')).sudo() if vals.get('parent_id') else None
            if parent and 'internal_permission' not in vals:
                vals['internal_permission'] = parent.internal_permission
            if parent and 'article_member_ids' not in vals:
                vals['article_member_ids'] = [
                    (0, 0, {'partner_id': m.partner_id.id, 'permission': m.permission})
                    for m in parent.article_member_ids]
        return super().create(vals_list)

    @api.model
    def article_create(self, title, parent_id=False):
        return self.create({'name': title, 'parent_id': parent_id}).id


class KnowledgeArticleMember(models.Model):
    _name = 'knowledge.article.member'
    _description = 'Knowledge article member (test stand-in)'

    article_id = fields.Many2one('knowledge.article', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', required=True)
    permission = fields.Selection([('write', 'Can edit'), ('read', 'Can read')], default='read')
