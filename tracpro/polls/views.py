from __future__ import absolute_import, unicode_literals

import cgi
import datetime
import json

from collections import OrderedDict, defaultdict
from dash.orgs.views import OrgPermsMixin, OrgObjPermsMixin
from dash.utils import datetime_to_ms, get_obj_cacheable
from django import forms
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartCRUDL, SmartCreateView, SmartReadView, SmartListView, SmartFormView
from tracpro.groups.models import Group
from .models import Poll, Issue, Response, RESPONSE_EMPTY, RESPONSE_PARTIAL, RESPONSE_COMPLETE
from .tasks import issue_restart_participants


class ChartJsDataEncoder(json.JSONEncoder):
    """
    JSON Encoder which encodes datetime objects millisecond timestamps. Used for highcharts.js data.
    """
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return datetime_to_ms(obj)
        return json.JSONEncoder.default(self, obj)


class PollCRUDL(SmartCRUDL):
    model = Poll
    actions = ('read', 'list', 'select')

    class Read(OrgObjPermsMixin, SmartReadView):

        def get_context_data(self, **kwargs):
            context = super(PollCRUDL.Read, self).get_context_data(**kwargs)
            questions = self.object.get_questions()

            issues = Issue.get_all(self.request.org, self.request.region, poll=self.object)

            # if we're viewing "All Regions" don't include regional only issues
            if not self.request.region:
                issues = issues.filter(region=None)

            # TODO this should be configurable based on date but for now let's just consider the last 10 issues
            issues = issues.order_by('-conducted_on')[0:10]

            for question in questions:
                categories = set()
                counts_by_issue = OrderedDict()

                # fetch category counts for all issues, keeping track of all found categories
                for issue in reversed(issues):
                    category_counts = issue.get_answer_category_counts(question, self.request.region)
                    counts_by_issue[issue] = category_counts

                    for category in category_counts.keys():
                        categories.add(category)

                categories = list(categories)
                category_series = defaultdict(list)

                for issue, category_counts in counts_by_issue.iteritems():
                    for category in categories:
                        count = category_counts.get(category, 0)
                        category_series[category].append((issue.conducted_on, count))

                chart_data = [dict(name=category, data=data) for category, data in category_series.iteritems()]

                question.chart_data = mark_safe(json.dumps(chart_data, cls=ChartJsDataEncoder))

            context['questions'] = questions
            return context

    class List(OrgPermsMixin, SmartListView):
        fields = ('name', 'questions', 'issues', 'last_conducted')
        default_order = ('name',)

        def derive_queryset(self, **kwargs):
            return Poll.get_all(self.request.org)

        def derive_issues(self, obj):
            return Issue.get_all(self.request.org, self.request.region, poll=obj)

        def get_questions(self, obj):
            return obj.get_questions().count()

        def get_issues(self, obj):
            return self.derive_issues(obj).count()

        def get_last_conducted(self, obj):
            last_issue = self.derive_issues(obj).order_by('-conducted_on').first()
            return last_issue.conducted_on if last_issue else _("Never")

    class Select(OrgPermsMixin, SmartFormView):
        class FlowsForm(forms.Form):
            flows = forms.MultipleChoiceField(choices=(), label=_("Flows"), help_text=_("Flows to track as polls."))

            def __init__(self, *args, **kwargs):
                org = kwargs.pop('org')

                super(PollCRUDL.Select.FlowsForm, self).__init__(*args, **kwargs)

                choices = []
                for flow in org.get_temba_client().get_flows(archived=False):
                    choices.append((flow.uuid, flow.name))

                self.fields['flows'].choices = choices
                self.fields['flows'].initial = Poll.get_all(org).order_by('name')

        title = _("Poll Flows")
        form_class = FlowsForm
        success_url = '@polls.poll_list'
        submit_button_name = _("Update")
        success_message = _("Updated flows to track as polls")

        def get_form_kwargs(self):
            kwargs = super(PollCRUDL.Select, self).get_form_kwargs()
            kwargs['org'] = self.request.org
            return kwargs

        def form_valid(self, form):
            Poll.sync_with_flows(self.request.org, form.cleaned_data['flows'])
            return HttpResponseRedirect(self.get_success_url())


class IssueListMixin(object):
    def get_participants(self, obj):
            counts = get_obj_cacheable(obj, '_response_counts', lambda: obj.get_response_counts(self.request.region))
            return counts[RESPONSE_EMPTY] + counts[RESPONSE_PARTIAL] + counts[RESPONSE_COMPLETE]

    def get_responses(self, obj):
        counts = get_obj_cacheable(obj, '_response_counts', lambda: obj.get_response_counts(self.request.region))
        return "%s / %s" % (counts[RESPONSE_PARTIAL], counts[RESPONSE_COMPLETE])

    def get_region(self, obj):
        return obj.region if obj.region else _("All")

    def lookup_field_link(self, context, field, obj):
        if field == 'poll' or field == 'conducted_on':
            return reverse('polls.issue_read', args=[obj.pk])
        elif field == 'participants':
            return reverse('polls.issue_participation', args=[obj.pk])
        elif field == 'responses':
            return reverse('polls.response_filter', kwargs=dict(issue=obj.pk))


class IssueCRUDL(SmartCRUDL):
    model = Issue
    actions = ('create', 'restart', 'read', 'participation', 'list', 'filter', 'latest')

    class Create(OrgPermsMixin, SmartCreateView):
        def post(self, request, *args, **kwargs):
            org = self.derive_org()
            poll = Poll.objects.get(org=org, pk=request.POST.get('poll'))
            issue = Issue.create_regional(self.request.user, poll, request.region, timezone.now(), do_start=True)
            return JsonResponse(issue.as_json(request.region))

    class Restart(OrgPermsMixin, SmartFormView):
        def post(self, request, *args, **kwargs):
            org = self.derive_org()
            issue = Issue.objects.get(poll__org=org, pk=request.POST.get('issue'))
            region = request.region

            incomplete_responses = issue.get_incomplete_responses(region)
            contact_uuids = [r.contact.uuid for r in incomplete_responses]

            issue_restart_participants.delay(issue.pk, contact_uuids)

            return JsonResponse({'contacts': len(contact_uuids)})

    class Read(OrgPermsMixin, SmartReadView):
        def get_queryset(self):
            return Issue.get_all(self.request.org, self.request.region)

        def get_context_data(self, **kwargs):
            context = super(IssueCRUDL.Read, self).get_context_data(**kwargs)
            questions = self.object.poll.get_questions()

            for question in questions:
                category_counts = self.object.get_answer_category_counts(question, self.request.region)

                # TODO what to do for questions that are open-ended. Do we also need to expose some more information
                # from flows.json to determine which questions are open-ended?
                # is_open_ended = len(category_counts.keys()) == 1

                chart_data = [[cgi.escape(category), count] for category, count in category_counts.iteritems()]

                question.chart_data = mark_safe(json.dumps(chart_data))

            context['questions'] = questions
            return context

    class Participation(OrgPermsMixin, SmartReadView):
        def get_queryset(self):
            return Issue.get_all(self.request.org, self.request.region)

        def get_context_data(self, **kwargs):
            context = super(IssueCRUDL.Participation, self).get_context_data(**kwargs)
            reporting_groups = Group.get_all(self.request.org).order_by('name')
            responses = self.object.get_responses(self.request.region)

            # initialize an ordered dict of group to response counts
            per_group_counts = OrderedDict()
            for reporting_group in reporting_groups:
                per_group_counts[reporting_group] = dict(E=0, P=0, C=0)

            no_group_counts = dict(E=0, P=0, C=0)
            overall_counts = dict(E=0, P=0, C=0)

            reporting_groups_by_id = {g.pk: g for g in reporting_groups}
            for response in responses:
                group_id = response.contact.group_id
                group = reporting_groups_by_id[group_id] if group_id else None
                status = response.status

                if group:
                    per_group_counts[group][status] += 1
                else:
                    no_group_counts[status] += 1

                overall_counts[status] += 1

            def calc_completion(counts):
                total = counts['E'] + counts['P'] + counts['C']
                return "%d%%" % int(100 * counts['C'] / total) if total else ''

            # for each set of counts, also calculate the completion percentage
            for group, counts in per_group_counts.iteritems():
                counts['X'] = calc_completion(counts)

            no_group_counts['X'] = calc_completion(no_group_counts)
            overall_counts['X'] = calc_completion(overall_counts)

            # participation table counts
            context['per_group_counts'] = per_group_counts
            context['no_group_counts'] = no_group_counts
            context['overall_counts'] = overall_counts

            # message recipient counts
            context['all_participants_count'] = overall_counts['E'] + overall_counts['P'] + overall_counts['C']
            context['incomplete_count'] = overall_counts['E'] + overall_counts['P']
            context['complete_count'] = overall_counts['C']
            return context

    class List(OrgPermsMixin, IssueListMixin, SmartListView):
        """
        All issues in current region
        """
        fields = ('poll', 'conducted_on', 'region', 'participants', 'responses')
        default_order = ('-conducted_on',)
        add_button = False  # TODO this doesn't work without https://github.com/nyaruka/smartmin/pull/48 so we're overriding the template as well
        link_fields = ('poll', 'participants', 'responses')

        def derive_title(self):
            return _("Poll Issues")

        def derive_queryset(self, **kwargs):
            return Issue.get_all(self.request.org, self.request.region)

    class Filter(OrgPermsMixin, IssueListMixin, SmartListView):
        """
        Issues filtered by poll
        """
        fields = ('conducted_on', 'region', 'participants', 'responses')
        link_fields = ('conducted_on', 'participants', 'responses')

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<poll>\d+)/$' % (path, action)

        def derive_poll(self):
            def fetch():
                return Poll.objects.get(pk=self.kwargs['poll'], org=self.request.org, is_active=True)
            return get_obj_cacheable(self, '_poll', fetch)

        def derive_queryset(self, **kwargs):
            return Issue.get_all(self.request.org, self.request.region).filter(poll=self.derive_poll())

        def get_context_data(self, **kwargs):
            context = super(IssueCRUDL.Filter, self).get_context_data(**kwargs)
            context['poll'] = self.derive_poll()
            return context

    class Latest(OrgPermsMixin, SmartListView):
        def get_queryset(self):
            return Issue.get_all(self.request.org, self.request.region).order_by('-conducted_on')[0:5]

        def render_to_response(self, context, **response_kwargs):
            results = [i.as_json(self.request.region) for i in context['object_list']]
            return JsonResponse({'count': len(results), 'results': results})


class ResponseCRUDL(SmartCRUDL):
    model = Response
    actions = ('filter',)

    class Filter(OrgPermsMixin, SmartListView):
        default_order = ('-created_on',)

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<issue>\d+)/$' % (path, action)

        def derive_issue(self):
            def fetch():
                return Issue.objects.select_related('poll').get(pk=self.kwargs['issue'], poll__org=self.request.org)
            return get_obj_cacheable(self, '_issue', fetch)

        def derive_questions(self):
            def fetch():
                questions = OrderedDict()
                for question in self.derive_issue().poll.get_questions():
                    questions['question_%d' % question.pk] = question
                return questions

            return get_obj_cacheable(self, '_questions', fetch)

        def derive_fields(self):
            base_fields = ['created_on', 'contact']
            if not self.request.region:
                base_fields.append('region')
            return base_fields + ['group'] + self.derive_questions().keys()

        def derive_link_fields(self, context):
            return 'contact',

        def derive_queryset(self, **kwargs):
            # only show partial and complete responses
            return self.derive_issue().get_responses(region=self.request.region, include_empty=False)

        def lookup_field_label(self, context, field, default=None):
            if field.startswith('question_'):
                question = self.derive_questions()[field]
                return question.text
            else:
                return super(ResponseCRUDL.Filter, self).lookup_field_label(context, field, default)

        def lookup_field_value(self, context, obj, field):
            if field == 'region':
                return obj.contact.region
            elif field == 'group':
                return obj.contact.group
            elif field.startswith('question_'):
                question = self.derive_questions()[field]
                answer = obj.answers.filter(question=question).first()
                return answer.value if answer else '--'
            else:
                return super(ResponseCRUDL.Filter, self).lookup_field_value(context, obj, field)

        def lookup_field_link(self, context, field, obj):
            if field == 'contact':
                return reverse('contacts.contact_read', args=[obj.contact.pk])

            return super(ResponseCRUDL.Filter, self).lookup_field_link(context, field, obj)

        def get_context_data(self, **kwargs):
            context = super(ResponseCRUDL.Filter, self).get_context_data(**kwargs)
            issue = self.derive_issue()

            # can only restart regional polls and if they're the last issue
            can_restart = self.request.region and issue.is_last_for_region(self.request.region)

            counts = issue.get_response_counts(self.request.region)

            context['issue'] = issue
            context['can_restart'] = can_restart
            context['response_count'] = counts[RESPONSE_EMPTY] + counts[RESPONSE_PARTIAL] + counts[RESPONSE_COMPLETE]
            context['complete_response_count'] = counts[RESPONSE_COMPLETE]
            context['incomplete_response_count'] = counts[RESPONSE_EMPTY] + counts[RESPONSE_PARTIAL]
            return context