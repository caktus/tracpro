from __future__ import unicode_literals

from dash.orgs.models import Org
from django.contrib.auth.models import User
from django.test import TestCase
from tracpro.contacts.models import Contact
from tracpro.groups.models import Group, Region
from uuid import uuid4


class TracProTest(TestCase):
    """
    Base class for all test cases in TracPro
    """
    def setUp(self):
        self.superuser = User.objects.create_superuser(username="root", email="super@user.com", password="root")

        # some orgs
        self.unicef = self.create_org("UNICEF", timezone="Asia/Kabul", subdomain="unicef")
        self.nyaruka = self.create_org("Nyaruka", timezone="Africa/Kigali", subdomain="nyaruka")

        # some admins for those orgs
        self.admin = self.create_admin(self.unicef, "Richard", "admin@unicef.org")
        self.eric = self.create_admin(self.nyaruka, "Eric", "eric@nyaruka.com")

        # some regions
        self.region1 = self.create_region(self.unicef, name="Kandahar", uuid='G-001')
        self.region2 = self.create_region(self.unicef, name="Khost", uuid='G-002')
        self.region3 = self.create_region(self.unicef, name="Kunar", uuid='G-003')
        self.region4 = self.create_region(self.nyaruka, name="Kigali", uuid='G-004')

        # some users in those regions
        self.user1 = self.create_user(self.unicef, "Sam Sims", "sam@unicef.org", regions=[self.region1])
        self.user2 = self.create_user(self.unicef, "Sue", "sue@unicef.org", regions=[self.region2, self.region3])
        self.user3 = self.create_user(self.nyaruka, "Nic", "nic@nyaruka.com", regions=[self.region4])

        # some reporting groups
        self.group1 = self.create_group(self.unicef, name="Farmers", uuid='G-005')
        self.group2 = self.create_group(self.unicef, name="Teachers", uuid='G-006')
        self.group3 = self.create_group(self.unicef, name="Doctors", uuid='G-007')
        self.group4 = self.create_group(self.nyaruka, name="Programmers", uuid='G-008')

        # some contacts
        self.contact1 = self.create_contact(self.unicef, "Ann", 'tel:1234', self.region1, self.group1, 'C-001')
        self.contact2 = self.create_contact(self.unicef, "Bob", 'tel:2345', self.region1, self.group1, 'C-002')
        self.contact3 = self.create_contact(self.unicef, "Cat", 'tel:3456', self.region1, self.group2, 'C-003')
        self.contact4 = self.create_contact(self.unicef, "Dan", 'twitter:danny', self.region2, self.group2, 'C-004')
        self.contact5 = self.create_contact(self.unicef, "Eve", 'twitter:evee', self.region3, self.group2, 'C-005')
        self.contact6 = self.create_contact(self.nyaruka, "Norbert", 'twitter:n7', self.region4, self.group4, 'C-006')

    def create_org(self, name, timezone, subdomain):
        return Org.objects.create(name=name, timezone=timezone, subdomain=subdomain, api_token=unicode(uuid4()),
                                  created_by=self.superuser, modified_by=self.superuser)

    def create_region(self, org, name, uuid):
        return Region.create(org, name, uuid)

    def create_group(self, org, name, uuid):
        return Group.create(org, name, uuid)

    def create_admin(self, org, full_name, email):
        user = User.create(None, full_name, email, password=email, change_password=False)
        user.org_admins.add(org)
        return user

    def create_user(self, org, full_name, email, regions):
        return User.create(org, full_name, email, password=email, change_password=False, regions=regions)

    def create_contact(self, org, name, urn, region, group, uuid):
        user = org.administrators.first()
        return Contact.create(org, user, name, urn, region, group, uuid)

    def login(self, user):
        result = self.client.login(username=user.username, password=user.username)
        self.assertTrue(result, "Couldn't login as %(user)s / %(user)s" % dict(user=user.username))

    def url_get(self, subdomain, url, params=None):
        if params is None:
            params = {}
        extra = {}
        if subdomain:
            extra['HTTP_HOST'] = '%s.localhost' % subdomain
        return self.client.get(url, params, **extra)

    def url_post(self, subdomain, url, data=None):
        if data is None:
            data = {}
        extra = {}
        if subdomain:
            extra['HTTP_HOST'] = '%s.localhost' % subdomain
        return self.client.post(url, data, **extra)

    def assertLoginRedirect(self, response, subdomain, next):
        self.assertRedirects(response, 'http://%s.localhost/users/login/?next=%s' % (subdomain, next))
