from __future__ import absolute_import, unicode_literals

import unicodecsv

from collections import OrderedDict
from dash.orgs.views import OrgPermsMixin, OrgObjPermsMixin
from dash.utils import datetime_to_ms, get_obj_cacheable
from django import forms
from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from smartmin.templatetags.smartmin import format_datetime
from smartmin.views import SmartCRUDL, SmartCreateView, SmartReadView, SmartListView, SmartFormView, SmartUpdateView
from tracpro.contacts.models import Contact
from tracpro.groups.models import Group
from .charts import multiple_pollruns, single_pollrun
from .models import Poll, Question, PollRun, Response, Window
from .models import QUESTION_TYPE_OPEN, QUESTION_TYPE_RECORDING, RESPONSE_EMPTY, RESPONSE_PARTIAL, RESPONSE_COMPLETE
from .tasks import pollrun_restart_participants


class PollForm(forms.ModelForm):
    """
    Form for contacts
    """
    name = forms.CharField(label=_("Name"))

    class Meta:
        model = Poll
        fields = forms.ALL_FIELDS

    def __init__(self, *args, **kwargs):
        super(PollForm, self).__init__(*args, **kwargs)

        for question in self.instance.get_questions():
            field_key = '__question__%d__text' % question.pk
            self.fields[field_key] = forms.CharField(max_length=255, initial=question.text,
                                                     label=_("Question #%d") % question.order)


class PollCRUDL(SmartCRUDL):
    model = Poll
    actions = ('read', 'update', 'list', 'select')

    class Read(OrgObjPermsMixin, SmartReadView):

        def get_context_data(self, **kwargs):
            context = super(PollCRUDL.Read, self).get_context_data(**kwargs)
            questions = self.object.get_questions()
            pollruns = self.object.get_pollruns(self.request.region)

            # if we're viewing "All Regions" don't include regional only pollruns
            if not self.request.region:
                pollruns = pollruns.filter(region=None)

            window = self.request.POST.get('window', self.request.GET.get('window', None))
            window = Window[window] if window else Window.last_30_days
            window_min, window_max = window.to_range()

            pollruns = pollruns.filter(conducted_on__gte=window_min, conducted_on__lt=window_max)
            pollruns = pollruns.order_by('conducted_on')

            for question in questions:
                question.chart_type, question.chart_data = multiple_pollruns(pollruns, question, self.request.region)

            context['window'] = window
            context['window_min'] = datetime_to_ms(window_min)
            context['window_max'] = datetime_to_ms(window_max)
            context['window_options'] = Window.__members__.values()
            context['questions'] = questions
            return context

    class Update(OrgObjPermsMixin, SmartUpdateView):
        exclude = ('is_active', 'flow_uuid', 'org')
        form_class = PollForm

        def post_save(self, obj):
            for field_key, value in self.form.cleaned_data.iteritems():
                if field_key.startswith('__question__'):
                    question_id = field_key.split('__')[2]
                    Question.objects.filter(pk=question_id, poll=self.object).update(text=value)

    class List(OrgPermsMixin, SmartListView):
        fields = ('name', 'questions', 'pollruns', 'last_conducted')
        field_config = {'pollruns': {'label': _("Dates")}}
        link_fields = ('name', 'pollruns')
        default_order = ('name',)

        def derive_queryset(self, **kwargs):
            return Poll.get_all(self.request.org)

        def derive_pollruns(self, obj):
            return obj.get_pollruns(self.request.region)

        def get_questions(self, obj):
            return obj.get_questions().count()

        def get_pollruns(self, obj):
            return self.derive_pollruns(obj).count()

        def get_last_conducted(self, obj):
            last_pollrun = self.derive_pollruns(obj).order_by('-conducted_on').first()
            return last_pollrun.conducted_on if last_pollrun else _("Never")

        def lookup_field_link(self, context, field, obj):
            if field == 'pollruns':
                return reverse('polls.pollrun_by_poll', args=[obj.pk])

            return super(PollCRUDL.List, self).lookup_field_link(context, field, obj)

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
                self.fields['flows'].initial = [p.flow_uuid for p in Poll.get_all(org)]

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


class PollRunListMixin(object):
    default_order = ('-conducted_on',)

    def get_conducted_on(self, obj):
        return obj.conducted_on.strftime(settings.SITE_DATE_FORMAT)

    def get_participants(self, obj):
            counts = get_obj_cacheable(obj, '_response_counts', lambda: obj.get_response_counts(self.request.region))
            return counts[RESPONSE_EMPTY] + counts[RESPONSE_PARTIAL] + counts[RESPONSE_COMPLETE]

    def get_responses(self, obj):
        counts = get_obj_cacheable(obj, '_response_counts', lambda: obj.get_response_counts(self.request.region))
        if counts[RESPONSE_PARTIAL]:
            return "%s (%s)" % (counts[RESPONSE_COMPLETE], counts[RESPONSE_PARTIAL])
        else:
            return counts[RESPONSE_COMPLETE]

    def get_region(self, obj):
        return obj.region if obj.region else _("All")

    def lookup_field_link(self, context, field, obj):
        if field == 'poll':
            return reverse('polls.poll_read', args=[obj.poll_id])
        if field == 'conducted_on':
            return reverse('polls.pollrun_read', args=[obj.pk])
        elif field == 'participants':
            return reverse('polls.pollrun_participation', args=[obj.pk])
        elif field == 'responses':
            return reverse('polls.response_by_pollrun', kwargs=dict(pollrun=obj.pk))


class PollRunCRUDL(SmartCRUDL):
    model = PollRun
    actions = ('create', 'restart', 'read', 'participation', 'list', 'by_poll', 'latest')

    class Create(OrgPermsMixin, SmartCreateView):
        def post(self, request, *args, **kwargs):
            org = self.derive_org()
            poll = Poll.objects.get(org=org, pk=request.POST.get('poll'))
            pollrun = PollRun.create_regional(self.request.user, poll, request.region, timezone.now(), do_start=True)
            return JsonResponse(pollrun.as_json(request.region))

    class Restart(OrgPermsMixin, SmartFormView):
        def post(self, request, *args, **kwargs):
            org = self.derive_org()
            pollrun = PollRun.objects.get(poll__org=org, pk=request.POST.get('pollrun'))
            region = request.region

            incomplete_responses = pollrun.get_incomplete_responses(region)
            contact_uuids = [r.contact.uuid for r in incomplete_responses]

            pollrun_restart_participants.delay(pollrun.pk, contact_uuids)

            return JsonResponse({'contacts': len(contact_uuids)})

    class Read(OrgPermsMixin, SmartReadView):
        def get_queryset(self):
            return PollRun.get_all(self.request.org, self.request.region)

        def get_context_data(self, **kwargs):
            context = super(PollRunCRUDL.Read, self).get_context_data(**kwargs)
            questions = self.object.poll.get_questions()

            for question in questions:
                question.chart_type, question.chart_data = single_pollrun(self.object, question, self.request.region)

            context['questions'] = questions
            return context

    class Participation(OrgPermsMixin, SmartReadView):
        def get_queryset(self):
            return PollRun.get_all(self.request.org, self.request.region)

        def get_context_data(self, **kwargs):
            context = super(PollRunCRUDL.Participation, self).get_context_data(**kwargs)
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

    class List(OrgPermsMixin, PollRunListMixin, SmartListView):
        """
        All pollruns in current region
        """
        fields = ('conducted_on', 'poll', 'region', 'participants', 'responses')
        link_fields = ('conducted_on', 'poll', 'participants', 'responses')
        add_button = False

        def derive_title(self):
            return _("All Poll Runs")

        def derive_queryset(self, **kwargs):
            return PollRun.get_all(self.request.org, self.request.region)

    class ByPoll(OrgPermsMixin, PollRunListMixin, SmartListView):
        """
        Poll Runs filtered by poll
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
            return self.derive_poll().get_pollruns(self.request.region)

        def get_context_data(self, **kwargs):
            context = super(PollRunCRUDL.ByPoll, self).get_context_data(**kwargs)
            context['poll'] = self.derive_poll()
            return context

    class Latest(OrgPermsMixin, SmartListView):
        def get_queryset(self):
            return PollRun.get_all(self.request.org, self.request.region).order_by('-conducted_on')[0:5]

        def render_to_response(self, context, **response_kwargs):
            results = [i.as_json(self.request.region) for i in context['object_list']]
            return JsonResponse({'count': len(results), 'results': results})


class ResponseCRUDL(SmartCRUDL):
    model = Response
    actions = ('by_pollrun', 'by_contact')

    class ByPollrun(OrgPermsMixin, SmartListView):
        default_order = ('-updated_on',)
        field_config = {'updated_on': {'label': _("Date")}}
        link_fields = ('contact',)

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<pollrun>\d+)/$' % (path, action)

        def derive_pollrun(self):
            def fetch():
                return PollRun.objects.select_related('poll').get(pk=self.kwargs['pollrun'], poll__org=self.request.org)
            return get_obj_cacheable(self, '_pollrun', fetch)

        def derive_questions(self):
            def fetch():
                questions = OrderedDict()
                for question in self.derive_pollrun().poll.get_questions():
                    questions['question_%d' % question.pk] = question
                return questions

            return get_obj_cacheable(self, '_questions', fetch)

        def derive_fields(self):
            base_fields = ['updated_on', 'contact']
            if not self.request.region:
                base_fields.append('region')
            return base_fields + ['group'] + self.derive_questions().keys()

        def derive_queryset(self, **kwargs):
            # only show partial and complete responses
            return self.derive_pollrun().get_responses(region=self.request.region, include_empty=False)

        def lookup_field_label(self, context, field, default=None):
            if field.startswith('question_'):
                question = self.derive_questions()[field]
                return question.text
            else:
                return super(ResponseCRUDL.ByPollrun, self).lookup_field_label(context, field, default)

        def lookup_field_value(self, context, obj, field):
            if field == 'region':
                return obj.contact.region
            elif field == 'group':
                return obj.contact.group
            elif field.startswith('question_'):
                question = self.derive_questions()[field]
                answer = obj.answers.filter(question=question).first()
                if answer:
                    if question.type == QUESTION_TYPE_RECORDING:
                        return '<a class="answer answer-audio" href="%s" data-answer-id="%d">Play</a>' % (
                            answer.value,
                            answer.pk,
                        )
                    else:
                        return answer.value
                else:
                    return '--'
            else:
                return super(ResponseCRUDL.ByPollrun, self).lookup_field_value(context, obj, field)

        def lookup_field_link(self, context, field, obj):
            if field == 'contact':
                return reverse('contacts.contact_read', args=[obj.contact.pk])

            return super(ResponseCRUDL.ByPollrun, self).lookup_field_link(context, field, obj)

        def get_context_data(self, **kwargs):
            context = super(ResponseCRUDL.ByPollrun, self).get_context_data(**kwargs)
            pollrun = self.derive_pollrun()
            context['pollrun'] = pollrun

            if '_format' not in self.request.POST and '_format' not in self.request.GET:
                # can only restart regional polls and if they're the last pollrun
                can_restart = self.request.region and pollrun.is_last_for_region(self.request.region)

                counts = pollrun.get_response_counts(self.request.region)

                context['can_restart'] = can_restart
                context['response_count'] = sum([
                    counts[RESPONSE_EMPTY],
                    counts[RESPONSE_PARTIAL],
                    counts[RESPONSE_COMPLETE],
                ])
                context['complete_response_count'] = counts[RESPONSE_COMPLETE]
                context['incomplete_response_count'] = sum([
                    counts[RESPONSE_EMPTY],
                    counts[RESPONSE_PARTIAL],
                ])
            return context

        def render_to_response(self, context, **response_kwargs):
            _format = self.request.POST.get('_format', self.request.GET.get('_format', None))

            if _format == 'csv':
                response = HttpResponse(content_type='text/csv', status=200)
                response['Content-Disposition'] = 'attachment; filename="responses.csv"'
                writer = unicodecsv.writer(response)

                questions = self.derive_questions().values()

                resp_headers = ['Date']
                contact_headers = ['Name', 'URN', 'Region', 'Group']
                question_headers = [q.text for q in questions]
                writer.writerow(resp_headers + contact_headers + question_headers)

                for resp in context['object_list']:
                    resp_cols = [format_datetime(resp.updated_on)]
                    contact_cols = [resp.contact.name, resp.contact.urn, resp.contact.region, resp.contact.group]
                    answer_cols = []

                    answers_by_question_id = {a.question_id: a for a in resp.answers.all()}
                    for question in questions:
                        answer = answers_by_question_id.get(question.pk, None)
                        answer_cols.append(answer.value if answer else '')

                    writer.writerow(resp_cols + contact_cols + answer_cols)

                return response
            else:
                return super(ResponseCRUDL.ByPollrun, self).render_to_response(context, **response_kwargs)

    class ByContact(OrgPermsMixin, SmartListView):
        fields = ('updated_on', 'poll', 'answers')
        field_config = {'updated_on': {'label': _("Date")}}
        link_fields = ('updated_on', 'poll')
        default_order = ('-updated_on',)

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<contact>\d+)/$' % (path, action)

        def derive_contact(self):
            def fetch():
                return Contact.objects.select_related('region').get(pk=self.kwargs['contact'], org=self.request.org)
            return get_obj_cacheable(self, '_contact', fetch)

        def derive_queryset(self, **kwargs):
            qs = self.derive_contact().get_responses(include_empty=True)

            return qs.select_related('pollrun__poll').prefetch_related('answers')

        def get_poll(self, obj):
            return obj.pollrun.poll

        def get_answers(self, obj):
            answers_by_q_id = {a.question_id: a for a in obj.answers.all()}
            answers = []

            if not answers_by_q_id:
                return '<i>%s</i>' % _("No response")

            questions = obj.pollrun.poll.get_questions()
            for question in questions:
                answer = answers_by_q_id.get(question.pk, None)
                if not answer:
                    answer_display = ""
                elif question.type == QUESTION_TYPE_OPEN:
                    answer_display = answer.value
                else:
                    answer_display = answer.category

                answers.append("%d. %s: <em>%s</em>" % (question.order, question.text, answer_display))

            return "<br/>".join(answers)

        def lookup_field_link(self, context, field, obj):
            if field == 'updated_on':
                return reverse('polls.pollrun_read', args=[obj.pollrun_id])
            elif field == 'poll':
                return reverse('polls.poll_read', args=[obj.pollrun.poll_id])

        def get_context_data(self, **kwargs):
            context = super(ResponseCRUDL.ByContact, self).get_context_data(**kwargs)
            context['contact'] = self.derive_contact()
            return context
