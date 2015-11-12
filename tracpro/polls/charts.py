from __future__ import absolute_import, unicode_literals

import cgi
from collections import defaultdict, OrderedDict
import datetime
from decimal import Decimal
import json
import operator

from dash.utils import datetime_to_ms

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
    if question.type == Question.TYPE_OPEN:
        word_counts = pollrun.get_answer_word_counts(question, regions)
        chart_type = 'word'
        chart_data = word_cloud_data(word_counts)
    elif question.type in (Question.TYPE_MULTIPLE_CHOICE, Question.TYPE_KEYPAD, Question.TYPE_MENU):
        category_counts = pollrun.get_answer_category_counts(question, regions)
        chart_type = 'pie'
        chart_data = pie_chart_data(category_counts)
    elif question.type == Question.TYPE_NUMERIC:
        range_counts = pollrun.get_answer_auto_range_counts(question, regions)
        chart_type = 'column'
        chart_data = column_chart_data(range_counts)
    else:
        chart_type = None
        chart_data = []

    return chart_type, render_data(chart_data)


def multiple_pollruns(pollruns, question, regions):
    """Chart data for multiple pollruns of a poll."""

    if question.type == Question.TYPE_OPEN:
        overall_counts = defaultdict(int)

        for pollrun in pollruns:
            word_counts = pollrun.get_answer_word_counts(question, regions)
            for word, count in word_counts:
                overall_counts[word] += count

        sorted_counts = sorted(
            overall_counts.items(), key=operator.itemgetter(1), reverse=True)
        chart_type = 'word'
        chart_data = word_cloud_data(sorted_counts[:50])
    elif question.type == Question.TYPE_MULTIPLE_CHOICE:
        categories = set()
        counts_by_pollrun = OrderedDict()

        # fetch category counts for all pollruns, keeping track of all found
        # categories
        for pollrun in pollruns:
            category_counts = pollrun.get_answer_category_counts(question, regions)
            as_dict = dict(category_counts)
            counts_by_pollrun[pollrun] = as_dict

            for category in as_dict.keys():
                categories.add(category)

        categories = list(categories)
        category_series = defaultdict(list)

        for pollrun, category_counts in counts_by_pollrun.iteritems():
            for category in categories:
                count = category_counts.get(category, 0)
                category_series[category].append((pollrun.conducted_on, count))

        chart_type = 'time-area'
        chart_data = [{'name': cgi.escape(category), 'data': data}
                      for category, data in category_series.iteritems()]
    elif question.type == Question.TYPE_NUMERIC:
        chart_type = 'time-line'
        chart_data = []
        for pollrun in pollruns:
            average = pollrun.get_answer_numeric_average(question, regions)
            chart_data.append((pollrun.conducted_on, average))
    else:
        chart_type = None
        chart_data = []

    return chart_type, render_data(chart_data)

def multiple_pollruns_new(pollruns, question, regions):
    """ Erin Test """
    """Chart data for multiple pollruns of a poll."""
    responses = Response.objects.filter(pollrun__in=pollruns)
    answers = Answer.objects.filter(response__in=responses, question=question)
    answer_list, date_list = answers.numeric_sum_group_by_date()

    return answer_list, date_list


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
