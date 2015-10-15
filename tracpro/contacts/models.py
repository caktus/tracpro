from __future__ import absolute_import, unicode_literals

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _

from dash.utils import intersection
from dash.utils.sync import ChangeType

from temba_client.types import Contact as TembaContact

from tracpro.groups.models import Region, Group

from .tasks import push_contact_change


class ContactQuerySet(models.QuerySet):

    def active(self):
        return self.filter(is_active=True)

    def by_org(self, org):
        return self.filter(org=org)

    def by_regions(self, regions):
        return self.filter(region__in=regions)


@python_2_unicode_compatible
class Contact(models.Model):
    """Corresponds to a RapidPro contact."""

    uuid = models.CharField(
        max_length=36, unique=True)
    org = models.ForeignKey(
        'orgs.Org', verbose_name=_("Organization"), related_name="contacts")
    name = models.CharField(
        verbose_name=_("Name"), max_length=128, blank=True,
        help_text=_("The name of this contact"))
    urn = models.CharField(
        verbose_name=_("URN"), max_length=255)
    region = models.ForeignKey(
        'groups.Region', verbose_name=_("Region"), related_name='contacts',
        help_text=_("Region or state this contact lives in"))
    group = models.ForeignKey(
        'groups.Group', null=True, verbose_name=_("Reporter group"),
        related_name='contacts')
    facility_code = models.CharField(
        max_length=160, verbose_name=_("Facility Code"), null=True, blank=True,
        help_text=_("Facility code for this contact"))
    language = models.CharField(
        max_length=3, verbose_name=_("Language"), null=True, blank=True,
        help_text=_("Language for this contact"))

    # Metadata.
    is_active = models.BooleanField(
        default=True, help_text=_("Whether this contact is active"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, related_name="contact_creations",
        help_text="The user which originally created this item")
    created_on = models.DateTimeField(
        auto_now_add=True,
        help_text="When this item was originally created")
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, related_name="contact_modifications",
        help_text="The user which last modified this item")
    modified_on = models.DateTimeField(
        auto_now=True,
        help_text="When this item was last modified")
    temba_modified_on = models.DateTimeField(
        null=True,
        help_text="When this item was last modified in Temba",
        editable=False)

    objects = ContactQuerySet.as_manager()

    def __str__(self):
        return self.name or self.get_urn()[1]

    def as_temba(self):
        """Return a Temba object representing this Contact."""
        groups = [self.region.uuid]
        if self.group_id:
            groups.append(self.group.uuid)

        temba_contact = TembaContact()
        temba_contact.name = self.name
        temba_contact.urns = [self.urn]
        temba_contact.fields = {
            self.org.facility_code_field: self.facility_code,
        }
        temba_contact.groups = groups
        temba_contact.language = self.language
        temba_contact.uuid = self.uuid
        return temba_contact

    @classmethod
    def get_or_fetch(cls, org, uuid):
        """Gets a contact by UUID.

        If we don't find them locally, we try to fetch them from RapidPro.
        """
        contacts = cls.objects.filter(org=org).select_related('region', 'group')
        try:
            return contacts.get(uuid=uuid)
        except cls.DoesNotExist:
            temba_contact = org.get_temba_client().get_contact(uuid)
            return cls.objects.create(**cls.kwargs_from_temba(org, temba_contact))

    def get_responses(self, include_empty=True):
        from tracpro.polls.models import Response
        qs = self.responses.filter(pollrun__poll__is_active=True, is_active=True)
        qs = qs.select_related('pollrun')
        if not include_empty:
            qs = qs.exclude(status=Response.STATUS_EMPTY)
        return qs

    def get_urn(self):
        return tuple(self.urn.split(':', 1))

    @classmethod
    def kwargs_from_temba(cls, org, temba_contact):
        """Get data to create a Contact instance from a Temba object."""
        # Set Use the first Temba Group that is used for a region.
        org_region_uuids = Region.get_all(org).values_list('uuid', flat=True)
        region_uuids = intersection(org_region_uuids, temba_contact.groups)
        region = Region.objects.get_all(org).filter(uuid=region_uuids[0])
        if not region:
            raise ValueError(
                "Unable to save contact {c.uuid} ({c.name}) because none of "
                "their groups match an active Region for this org: "
                "{groups}".format(
                    c=temba_contact,
                    groups=', '.join(temba_contact.groups)))

        # Set contact's reporter group to first Temba group that matches a
        # known reporter group.
        org_group_uuids = Group.get_all(org).values_list('uuid', flat=True)
        group_uuids = intersection(org_group_uuids, temba_contact.groups)
        group = Group.objects.get_all(org).filter(uuid=group_uuids[0])

        return {
            'org': org,
            'name': temba_contact.name,
            'urn': temba_contact.urns[0],
            'region': region,
            'group': group,
            'language': temba_contact.language,
            'facility_code': temba_contact.fields.get(org.facility_code_field, None),
            'uuid': temba_contact.uuid,
            'temba_modified_on': temba_contact.modified_on,
        }

    def push(self, change_type):
        push_contact_change.delay(self.pk, change_type)

    def release(self):
        self.is_active = False
        self.save()
        self.push(ChangeType.deleted)

    def clean(self):
        if self.org.pk != self.region.org_id:
            raise ValidationError("Region does not belong to Org.")
        if self.group and self.org.pk != self.group.org_id:
            raise ValidationError("Group does not belong to Org.")

    def save(self, *args, **kwargs):
        self.name = self.name or ""  # RapidPro might return blank or null values.
        return super(Contact, self).save(*args, **kwargs)
