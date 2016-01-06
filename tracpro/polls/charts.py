from __future__ import absolute_import, unicode_literals

import cgi
from collections import defaultdict
import datetime
from decimal import Decimal
from itertools import groupby
import json
import numpy
from operator import itemgetter

from dash.utils import datetime_to_ms

from django.db.models import Count, F
from django.core.urlresolvers import reverse
from django.utils.safestring import mark_safe

from .models import Answer, Question, Response


class ChartJsonEncoder(json.JSONEncoder):
    """Encode millisecond timestamps & Decimal objects as floats."""

    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return datetime_to_ms(obj)
        elif isinstance(obj, Decimal):
            return float(obj)
        return json.JSONEncoder.default(self, obj)


def single_pollrun(pollrun, question, regions):
    """Chart data for a single pollrun.

    Will be a word cloud for open-ended questions, and pie chart of categories
    for everything else.
    """
    if question.question_type == Question.TYPE_OPEN:
        word_counts = pollrun.get_answer_word_counts(question, regions)
        chart_type = 'word'
        chart_data = word_cloud_data(word_counts)
    elif question.question_type in (Question.TYPE_MULTIPLE_CHOICE, Question.TYPE_KEYPAD, Question.TYPE_MENU):
        category_counts = pollrun.get_answer_category_counts(question, regions)
        chart_type = 'pie'
        chart_data = pie_chart_data(category_counts)
    elif question.question_type == Question.TYPE_NUMERIC:
        range_counts = pollrun.get_answer_auto_range_counts(question, regions)
        chart_type = 'column'
        chart_data = column_chart_data(range_counts)
    else:
        chart_type = None
        chart_data = []

    return chart_type, render_data(chart_data)


def multiple_pollruns(pollruns, question, regions):
    if question.question_type == Question.TYPE_NUMERIC:
        chart_type = 'numeric'
        data = multiple_pollruns_numeric(pollruns, question, regions)

    elif question.question_type == Question.TYPE_OPEN:
        chart_type = 'open-ended'
        data = multiple_pollruns_open(pollruns, question, regions)

    elif question.question_type == Question.TYPE_MULTIPLE_CHOICE:
        chart_type = 'multiple-choice'
        data = multiple_pollruns_multiple_choice(pollruns, question, regions)

    else:
        chart_type = None
        data = None

    return chart_type, render_data(data) if data else None


def multiple_pollruns_open(pollruns, question, regions):
    """Chart data for multiple pollruns of a poll."""
    # {'word1': 50, 'word2': 9, ...}
    counts = defaultdict(int)
    for pollrun in pollruns:
        for word, count in pollrun.get_answer_word_counts(question, regions):
            counts[word] += count

    if counts:
        sorted_counts = sorted(counts.items(), key=itemgetter(1), reverse=True)
        return word_cloud_data(sorted_counts[:50])
    return None


def multiple_pollruns_multiple_choice(pollruns, question, regions):
    answers = Answer.objects.filter(response__pollrun=pollruns, response__is_active=True)
    answers = answers.exclude(response__status=Response.STATUS_EMPTY)
    if regions:
        answers = answers.filter(response__contact__region__in=regions)

    if answers:
        # Find the distinct categories for all answers to the question.
        categories = answers.distinct('category')
        categories = categories.order_by('category').values_list('category', flat=True)

        # category: [day_1_value, day_2_value, ...]
        series = []
        for category in categories:
            category_counts = [answers.filter(category=category, response__pollrun=pollrun).count()
                               for pollrun in pollruns.order_by('conducted_on')]
            series.append({'name': category, 'data': category_counts})

        # [day_1, day_2, ...]
        dates = [pollrun.conducted_on.strftime('%Y-%m-%d')
                 for pollrun in pollruns.order_by('conducted_on')]

        return {
            'dates': dates,
            'series': series,
        }
    return None


def response_rate_calculation(responses, pollrun_list):
    """Return a list of response rates for the pollruns."""
    # A response is complete if its status attribute equals STATUS_COMPLETE.
    # This uses an internal, _combine, because F expressions have not
    # exposed the SQL '=' operator.
    is_complete = F('status')._combine(Response.STATUS_COMPLETE, '=', False)
    responses = responses.annotate(is_complete=is_complete)

    # Count responses by completion status per pollrun.
    # When an annotation is applied to a values() result, the annotation
    # results are grouped by the unique combinations of the fields specified
    # in the values() clause. Result looks like:
    #   [
    #       {'pollrun': 123, 'is_complete': True, 'count': 5},
    #       {'pollrun': 123, 'is_complete': False, 'count': 10},
    #       {'pollrun': 456, 'is_complete': True, 'count': 7},
    #       {'pollrun': 456, 'is_complete': False, 'count': 12},
    #       ...
    #   ]
    responses = responses.order_by('pollrun')
    responses = responses.values('pollrun', 'is_complete')
    responses = responses.annotate(count=Count('pk'))

    # pollrun id -> response data
    data_by_pollrun = groupby(responses, itemgetter('pollrun'))
    data_by_pollrun = dict((k, list(v)) for k, v in data_by_pollrun)

    response_rates = []
    for pollrun in pollrun_list:
        response_data = data_by_pollrun.get(pollrun)
        if response_data:
            # completion status (True/False) -> count of responses
            count_by_status = dict((c['is_complete'], c['count']) for c in response_data)

            complete_responses = count_by_status.get(True, 0)
            total_responses = sum(count_by_status.values())
            response_rates.append(round(100.0 * complete_responses / total_responses, 2))
        else:
            response_rates.append(0)
    return response_rates


def multiple_pollruns_numeric(pollruns, question, regions):
    """Chart data for multiple pollruns of a poll."""
    responses = Response.objects.filter(pollrun__in=pollruns)
    responses = responses.filter(is_active=True)
    if regions:
        responses = responses.filter(contact__region__in=regions)

    answers = Answer.objects.filter(response__in=responses, question=question)
    answers = answers.select_related('response')
    answers = answers.order_by('response__created_on')

    if answers:
        # Calculate/retrieve the list of sums, list of averages,
        # list of pollrun dates, and list of pollrun id's
        # per pollrun date
        (answer_sum_list, answer_average_list,
            date_list, pollrun_list) = answers.numeric_group_by_date()

        # Calculate the response rate on each day
        response_rate_list = response_rate_calculation(responses, pollrun_list)

        # Create dict lists for the three datasets for data point/url
        answer_sum_dict_list = []
        answer_average_dict_list = []
        response_rate_dict_list = []
        for z in zip(answer_sum_list, answer_average_list, response_rate_list, pollrun_list):
            pollrun_link_read = reverse('polls.pollrun_read', args=[str(z[3])])
            pollrun_link_participation = reverse('polls.pollrun_participation', args=[str(z[3])])
            answer_sum_dict_list.append(
                {str('y'): z[0], str('url'): pollrun_link_read})
            answer_average_dict_list.append(
                {str('y'): z[1], str('url'): pollrun_link_read})
            response_rate_dict_list.append(
                {str('y'): z[2], str('url'): pollrun_link_participation})

        question.answer_mean = round(numpy.mean(answer_average_list), 2)
        question.answer_stdev = round(numpy.std(answer_average_list), 2)
        question.response_rate_average = round(numpy.mean(response_rate_list), 2)
        return {
            'dates': [d.strftime('%Y-%m-%d') for d in date_list],
            'sum': answer_sum_dict_list,
            'average': answer_average_dict_list,
            'response-rate': response_rate_dict_list,
        }
    return None


def word_cloud_data(word_counts):
    return [{'text': word, 'weight': count} for word, count in word_counts]


def pie_chart_data(category_counts):
    return [[cgi.escape(category), count] for category, count in category_counts]


def column_chart_data(range_counts):
    # highcharts needs the category labels and values separate for column charts
    if range_counts:
        labels, counts = zip(*range_counts)
        return [cgi.escape(l) for l in labels], counts
    else:
        return []


def render_data(chart_data):
    return mark_safe(json.dumps(chart_data, cls=ChartJsonEncoder))
