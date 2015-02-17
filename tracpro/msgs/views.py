from __future__ import absolute_import, unicode_literals

from dash.orgs.views import OrgPermsMixin
from django.core.urlresolvers import reverse
from django.http import JsonResponse
from django.utils.translation import ugettext_lazy as _
from smartmin.users.views import SmartCRUDL, SmartListView, SmartCreateView
from tracpro.polls.models import Issue
from .models import Message


class MessageCRUDL(SmartCRUDL):
    model = Message
    actions = ('list', 'send')

    class List(OrgPermsMixin, SmartListView):
        fields = ('sent_on', 'sent_by', 'issue', 'cohort', 'region', 'text')
        field_config = {'text': {'class': 'italicized'},
                        'cohort': {'label': _("Recipients")},
                        'issue': {'label': _("Poll Issue")}}
        title = _("Message Log")
        default_order = ('-pk',)
        link_fields = ('issue',)

        def derive_queryset(self, **kwargs):
            return Message.get_all(self.request.org, self.request.region)

        def get_cohort(self, obj):
            return obj.get_cohort_display()

        def get_region(self, obj):
            return obj.region if obj.region else _("All")

        def lookup_field_link(self, context, field, obj):
            if field == 'issue':
                return reverse('polls.issue_read', args=[obj.issue.pk])

            return super(MessageCRUDL.List, self).lookup_field_link(context, field, obj)

    class Send(OrgPermsMixin, SmartCreateView):
        def post(self, request, *args, **kwargs):
            org = self.derive_org()
            text = request.POST.get('text')
            cohort = request.POST.get('cohort')
            issue = Issue.objects.get(poll__org=org, pk=request.POST.get('issue'))
            region = self.request.region

            msg = Message.create(org, self.request.user, text, issue, cohort, region)
            return JsonResponse(msg.as_json())
