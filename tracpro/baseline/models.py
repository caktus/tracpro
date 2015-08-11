from django.db import models
from django.utils.translation import ugettext_lazy as _

from smart_selects.db_fields import ChainedForeignKey

from tracpro.polls.models import Answer, Question, Poll


class BaselineTerm(models.Model):
    """
    e.g., 2015 Term 3 Attendance for P3 Girls
     A term of time to gather statistics for a baseline chart
     baseline_question: the answer to this will determine our baseline
                        information for all dates
                        ie. How many students are enrolled?
     follow_up_question: the answers to this question will determine the
                         follow-up information, over the date range (start_date -> end_date)
                         ie. How many students attended today?
    """

    org = models.ForeignKey("orgs.Org", verbose_name=_("Organization"), related_name="baseline_terms")
    name = models.CharField(max_length=255, help_text=_("For example: 2015 Term 3 Attendance for P3 Girls"))
    start_date = models.DateField()
    end_date = models.DateField()

    baseline_poll = models.ForeignKey(Poll, related_name="baseline_terms")
    baseline_question = ChainedForeignKey(
                        Question,
                        chained_field='baseline_poll',
                        chained_model_field='poll',
                        auto_choose=True,
                        related_name="baseline_terms"
                        )

    follow_up_poll = models.ForeignKey(Poll)
    follow_up_question = ChainedForeignKey(
                            Question,
                            chained_field='follow_up_poll',
                            chained_model_field='poll',
                            auto_choose=True
                            )

    def get_baseline(self, region):
        answers = Answer.objects.filter(
            response__contact__region=region,
            question=self.baseline_question,
            submitted_on__gte=self.start_date,
            submitted_on__lte=self.end_date,  # look into timezone
        )
        answers = answers.order_by("submitted_on")
        baseline_answer = answers.last()
        return baseline_answer.value

    def get_follow_up(self, region):
        answers = Answer.objects.filter(
            response__contact__region=region,
            question=self.follow_up_question,
            submitted_on__gte=self.start_date,
            submitted_on__lte=self.end_date,  # look into timezone
        )
        answers = answers.order_by("submitted_on")
        return list(answers.values_list("submitted_on", "value"))
