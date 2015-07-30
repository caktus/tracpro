from django import forms
from django.utils.translation import ugettext_lazy as _

from dash.orgs.forms import OrgForm


class SimpleOrgEditForm(OrgForm):
    facility_code_field = forms.ChoiceField(
        choices=(), label=_("Facility code field"),
        help_text=_("Contact field to use as the facility code."))

    def __init__(self, *args, **kwargs):
        super(SimpleOrgEditForm, self).__init__(*args, **kwargs)
        field_choices = [(f.key, '{} ({})'.format(f.label, f.key))
                         for f in self.instance.get_temba_client().get_fields()]
        self.fields['facility_code_field'].choices = field_choices
        self.fields['facility_code_field'].initial = self.instance.facility_code_field

    class Meta(OrgForm.Meta):
        fields = ('name', 'timezone')

    def save(self, *args, **kwargs):
        self.instance.facility_code_field = self.cleaned_data['facility_code_field']
        return super(SimpleOrgEditForm, self).save(*args, **kwargs)
