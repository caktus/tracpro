from django import forms

from tracpro.polls.models import Poll
from .models import BaselineTerm


class BaselineTermForm(forms.ModelForm):
    """
    Form for Baseline Term
    """
    class Meta:
        model = BaselineTerm
        fields = ('name', 'org', 'start_date', 'end_date',
                  'baseline_poll', 'baseline_question',
                  'follow_up_poll', 'follow_up_question')

        widgets = {
            'start_date': forms.widgets.DateInput(attrs={'class': 'datepicker'}),
            'end_date': forms.widgets.DateInput(attrs={'class': 'datepicker'}),
            'org': forms.HiddenInput()
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        org = self.user.get_org()

        super(BaselineTermForm, self).__init__(*args, **kwargs)

        if org:
            polls = Poll.get_all(org).order_by('name')
            self.fields['baseline_poll'].queryset = polls
            self.fields['follow_up_poll'].queryset = polls

    def clean(self, *args, **kwargs):
        cleaned_data = super(BaselineTermForm, self).clean()

        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError(
                "Start date should be before end date."
            )

        baseline_question = cleaned_data.get("baseline_question")
        follow_up_question = cleaned_data.get("follow_up_question")

        if baseline_question and follow_up_question and baseline_question == follow_up_question:
            raise forms.ValidationError(
                "Baseline question and follow up question should be different."
            )

        return cleaned_data
