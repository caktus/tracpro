from __future__ import unicode_literals

import json

from dateutil.relativedelta import relativedelta

from django.core.urlresolvers import reverse
from django.utils import timezone

from tracpro.polls import models as polls
from tracpro.test import factories
from tracpro.test.cases import TracProDataTest, TracProTest

from .. import models


class TestRegionList(TracProDataTest):
    url_name = "groups.region_list"

    def test_list_non_admin(self):
        self.login(self.user1)  # not an admin
        url = reverse(self.url_name)
        response = self.url_get('unicef', url)
        self.assertLoginRedirect(response, "unicef", url)

    def test_list_admin(self):
        self.login(self.admin)
        response = self.url_get('unicef', reverse(self.url_name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['object_list']), 3)


class TestRegionMostActive(TracProDataTest):
    url_name = "groups.region_most_active"

    def test_most_active(self):
        five_weeks_ago = timezone.now() - relativedelta(weeks=5)
        five_days_ago = timezone.now() - relativedelta(days=5)

        pollrun = polls.PollRun.objects.create(
            poll=self.poll1,
            conducted_on=five_weeks_ago,
        )

        # empty response in last month for contact in region #1
        polls.Response.objects.create(
            flow_run_id=123, pollrun=pollrun, contact=self.contact1,
            created_on=five_days_ago, updated_on=five_days_ago,
            status=polls.RESPONSE_EMPTY)

        # partial response not in last month for contact in region #2
        polls.Response.objects.create(
            flow_run_id=234, pollrun=pollrun, contact=self.contact4,
            created_on=five_weeks_ago, updated_on=five_weeks_ago,
            status=polls.RESPONSE_PARTIAL)

        # partial response in last month for contact in region #2
        polls.Response.objects.create(
            flow_run_id=345, pollrun=pollrun, contact=self.contact4,
            created_on=five_days_ago, updated_on=five_days_ago,
            status=polls.RESPONSE_PARTIAL)

        # 2 complete responses in last month for contact in region #3
        polls.Response.objects.create(
            flow_run_id=456, pollrun=pollrun, contact=self.contact5,
            created_on=five_days_ago, updated_on=five_days_ago,
            status=polls.RESPONSE_COMPLETE)

        polls.Response.objects.create(
            flow_run_id=567, pollrun=pollrun, contact=self.contact5,
            created_on=five_days_ago, updated_on=five_days_ago,
            status=polls.RESPONSE_COMPLETE)

        # log in as a non-administrator
        self.login(self.user1)

        response = self.url_get('unicef', reverse(self.url_name))
        results = json.loads(response.content)['results']
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['id'], self.region3.pk)
        self.assertEqual(results[0]['name'], self.region3.name)
        self.assertEqual(results[0]['response_count'], 2)
        self.assertEqual(results[1]['id'], self.region2.pk)
        self.assertEqual(results[1]['name'], self.region2.name)
        self.assertEqual(results[1]['response_count'], 1)


class TestRegionUpdateHierarchy(TracProTest):
    url_name = "groups.region_update_hierarchy"

    def setUp(self):
        super(TestRegionUpdateHierarchy, self).setUp()

        self.user = factories.User()
        self.login(self.user)

        self.org = factories.Org(name="Test", subdomain="test")
        self.org.administrators.add(self.user)

    def assertErrorResponse(self, data, message):
        """Assert that the data causes an error with the given message."""
        response = self.url_post("test", reverse(self.url_name), data=data)
        self.assertEqual(response.status_code, 200)

        content = json.loads(response.content.decode("utf-8"))
        self.assertFalse(content['success'])
        self.assertEqual(content['status'], 400)
        self.assertEqual(content['message'], message)

    def assertSuccessResponse(self, data, expected_structure):
        """Assert that region hierarchy is successfully updated."""
        response = self.url_post("test", reverse(self.url_name), data=data)
        self.assertEqual(response.status_code, 200)

        content = json.loads(response.content.decode("utf-8"))
        self.assertTrue(content['success'])
        self.assertEqual(content['status'], 200)
        self.assertEqual(
            content['message'],
            "Test region hierarchy has been updated.")

        regions = models.Region.get_all(self.org)
        new_structure = dict(regions.values_list('pk', 'parent'))
        self.assertDictEqual(expected_structure, new_structure)

    def make_regions(self):
        """Create a collection of nested regions."""
        self.region_uganda = factories.Region(
            org=self.org, name="Uganda")
        self.region_kampala = factories.Region(
            org=self.org, name="Kampala", parent=self.region_uganda)
        self.region_makerere = factories.Region(
            org=self.org, name="Makerere", parent=self.region_kampala)
        self.region_entebbe = factories.Region(
            org=self.org, name="Entebbe", parent=self.region_uganda)
        self.region_kenya = factories.Region(
            org=self.org, name="Kenya")
        self.region_nairobi = factories.Region(
            org=self.org, name="Nairobi", parent=self.region_kenya)
        self.region_mombasa = factories.Region(
            org=self.org, name="Mombasa", parent=self.region_kenya)
        return models.Region.get_all(self.org)

    def test_unauthenticated(self):
        """View requires authentication."""
        self.client.logout()
        url = reverse(self.url_name)
        response = self.url_get("test", url)
        self.assertLoginRedirect(response, "test", url)

    def test_no_org(self):
        """View must be used with a specific org."""
        response = self.url_get(None, reverse(self.url_name))
        self.assertRedirects(response, reverse("orgs_ext.org_chooser"))

    def test_no_perms(self):
        """View requires that the user is an org administrator."""
        self.org.administrators.remove(self.user)
        url = reverse(self.url_name)
        response = self.url_get("test", url)
        self.assertLoginRedirect(response, "test", url)

    def test_editor(self):
        """View requires that the user is an org administrator."""
        self.org.administrators.remove(self.user)
        self.org.editors.add(self.user)
        url = reverse(self.url_name)
        response = self.url_get("test", url)
        self.assertLoginRedirect(response, "test", url)

    def test_viewer(self):
        """View requires that the user is an org administrator."""
        self.org.administrators.remove(self.user)
        self.org.viewers.add(self.user)
        url = reverse(self.url_name)
        response = self.url_get("test", url)
        self.assertLoginRedirect(response, "test", url)

    def test_get(self):
        """View is post-only."""
        response = self.url_get("test", reverse(self.url_name))
        self.assertEqual(response.status_code, 405)

    def test_post_no_data(self):
        """View requires that data is sent in the `data` parameter."""
        self.assertErrorResponse(
            data={},
            message="No data was provided in the `data` parameter.")

    def test_post_invalid_json_data(self):
        """View requires valid JSON data in the `data` parameter."""
        self.assertErrorResponse(
            data={'data': "invalid"},
            message="Data must be valid JSON.")

    def test_post_wrong_type(self):
        """View requires a JSON-encoded dictionary in the `data` parameter."""
        self.assertErrorResponse(
            data={'data': json.dumps("Wrong type")},
            message="Data must be a dict that maps region id to parent id.")

    def test_post_extra_groups(self):
        """Submitted data should provide data for all groups in the org."""
        regions = self.make_regions()
        structure = dict(regions.values_list('pk', 'parent'))
        structure['12345'] = str(regions.first().pk)
        self.assertErrorResponse(
            data={'data': json.dumps(structure)},
            message="Data must map region id to parent id for each region "
                    "in this org.")

    def test_post_missing_groups(self):
        """Submitted data should provide data for all groups in the org."""
        regions = self.make_regions()
        structure = dict(regions.values_list('pk', 'parent'))
        structure.pop(regions.first().pk)
        self.assertErrorResponse(
            data={'data': json.dumps(structure)},
            message="Data must map region id to parent id for each region "
                    "in this org.")

    def test_post_invalid_parent(self):
        """Submitted data should only reference parents within the same org."""
        regions = self.make_regions()
        structure = dict(regions.values_list('pk', 'parent'))
        structure[regions.first().pk] = "12345"
        self.assertErrorResponse(
            data={'data': json.dumps(structure)},
            message="Region parent must be a region from the same org, or "
                    "null.")

    def test_post_same(self):
        """Test when hierarchy is "updated" to existing hierarchy."""
        regions = self.make_regions()
        structure = dict(regions.values_list('pk', 'parent'))
        data = {'data': json.dumps(structure)}
        self.assertSuccessResponse(data, structure)

    def test_post_change(self):
        """Test hierarchy change."""
        regions = self.make_regions()
        structure = dict(regions.values_list('pk', 'parent'))
        structure[self.region_kampala.pk] = self.region_kenya.pk
        structure[self.region_nairobi.pk] = self.region_uganda.pk
        data = {'data': json.dumps(structure)}
        self.assertSuccessResponse(data, structure)


class TestGroupList(TracProDataTest):
    url_name = "groups.group_list"

    def test_non_admin(self):
        self.login(self.user1)  # not an admin
        url = reverse(self.url_name)
        response = self.url_get('unicef', url)
        self.assertLoginRedirect(response, "unicef", url)

    def test_admin(self):
        self.login(self.admin)
        response = self.url_get('unicef', reverse(self.url_name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['object_list']), 3)


class TestGroupMostActive(TracProDataTest):
    url_name = "groups.group_most_active"

    def test_most_active(self):
        five_weeks_ago = timezone.now() - relativedelta(weeks=5)
        five_days_ago = timezone.now() - relativedelta(days=5)
        pollrun = polls.PollRun.objects.create(
            poll=self.poll1,
            conducted_on=five_weeks_ago,
        )

        # empty response in last month for contact in group #1
        polls.Response.objects.create(
            flow_run_id=123, pollrun=pollrun, contact=self.contact1,
            created_on=five_days_ago, updated_on=five_days_ago,
            status=polls.RESPONSE_EMPTY)

        # partial response not in last month for contact in group #2
        polls.Response.objects.create(
            flow_run_id=234, pollrun=pollrun, contact=self.contact3,
            created_on=five_weeks_ago, updated_on=five_weeks_ago,
            status=polls.RESPONSE_PARTIAL)

        # partial response in last month for contact in group #2
        polls.Response.objects.create(
            flow_run_id=345, pollrun=pollrun, contact=self.contact3,
            created_on=five_days_ago, updated_on=five_days_ago,
            status=polls.RESPONSE_PARTIAL)

        # 2 complete responses in last month for contact in group #3
        polls.Response.objects.create(
            flow_run_id=456, pollrun=pollrun, contact=self.contact5,
            created_on=five_days_ago, updated_on=five_days_ago,
            status=polls.RESPONSE_COMPLETE)

        polls.Response.objects.create(
            flow_run_id=567, pollrun=pollrun, contact=self.contact5,
            created_on=five_days_ago, updated_on=five_days_ago,
            status=polls.RESPONSE_COMPLETE)

        # log in as a non-administrator
        self.login(self.user1)

        response = self.url_get('unicef', reverse(self.url_name))
        results = json.loads(response.content)['results']
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['id'], self.group3.pk)
        self.assertEqual(results[0]['name'], self.group3.name)
        self.assertEqual(results[0]['response_count'], 2)
        self.assertEqual(results[1]['id'], self.group2.pk)
        self.assertEqual(results[1]['name'], self.group2.name)
        self.assertEqual(results[1]['response_count'], 1)
