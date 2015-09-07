# coding=utf-8
from __future__ import absolute_import, unicode_literals

from django.core.urlresolvers import reverse

from tracpro.test.cases import TracProDataTest

from ..models import (
    Answer, PollRun, Response)


class PollCRUDLTest(TracProDataTest):

    def test_list(self):
        url = reverse('polls.poll_list')

        # log in as admin
        self.login(self.admin)

        response = self.url_get('unicef', url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['object_list']), 1)


class ResponseCRUDLTest(TracProDataTest):

    def setUp(self):
        super(ResponseCRUDLTest, self).setUp()
        date1 = self.datetime(2014, 1, 1, 7, 0)
        date2 = self.datetime(2014, 1, 1, 8, 0)
        date3 = self.datetime(2014, 1, 2, 7, 0)

        # create non-regional pollrun with 3 responses (1 complete, 1 partial, 1 empty)
        self.pollrun1 = PollRun.objects.create(
            poll=self.poll1, region=None, conducted_on=date1)

        self.pollrun1_r1 = Response.objects.create(
            flow_run_id=123, pollrun=self.pollrun1, contact=self.contact1,
            created_on=date1, updated_on=date1, status=Response.STATUS_COMPLETE)
        Answer.create(
            self.pollrun1_r1, self.poll1_question1, "5.0000", "1 - 10", date1)
        Answer.create(
            self.pollrun1_r1, self.poll1_question2, "Sunny", "All Responses", date1)

        self.pollrun1_r2 = Response.objects.create(
            flow_run_id=234, pollrun=self.pollrun1, contact=self.contact2,
            created_on=date2, updated_on=date2, status=Response.STATUS_PARTIAL)
        Answer.create(
            self.pollrun1_r2, self.poll1_question1, "6.0000", "1 - 10", date2)

        self.pollrun1_r3 = Response.objects.create(
            flow_run_id=345, pollrun=self.pollrun1, contact=self.contact4,
            created_on=date3, updated_on=date3, status=Response.STATUS_EMPTY)

        # create regional pollrun with 1 incomplete response
        self.pollrun2 = PollRun.objects.create(
            poll=self.poll1, region=self.region1, conducted_on=date3)
        self.pollrun2_r1 = Response.objects.create(
            flow_run_id=456, pollrun=self.pollrun2, contact=self.contact1,
            created_on=date3, updated_on=date3, status=Response.STATUS_PARTIAL)

    def test_by_pollrun(self):
        url = reverse('polls.response_by_pollrun', args=[self.pollrun1.pk])

        # log in as admin
        self.login(self.admin)

        # view responses for pollrun #1
        response = self.url_get('unicef', url)
        self.assertContains(response, "Number of sheep", status_code=200)
        self.assertContains(response, "How is the weather?")

        responses = list(response.context['object_list'])
        self.assertEqual(len(responses), 2)
        # newest non-empty first
        self.assertEqual(responses, [self.pollrun1_r2, self.pollrun1_r1])

        # can't restart from "All Regions" view of responses
        self.assertFalse(response.context['can_restart'])

        self.switch_region(self.region1)

        # can't restart as there is a later pollrun of the same poll in region #1
        response = self.url_get('unicef', url)
        self.assertFalse(response.context['can_restart'])

        self.switch_region(self.region2)

        # can restart as this is the latest pollrun of this poll in region #2
        response = self.url_get('unicef', url)
        self.assertTrue(response.context['can_restart'])

    def test_by_contact(self):
        # log in as admin
        self.login(self.admin)

        # view responses for contact #1
        url = reverse('polls.response_by_contact', args=[self.contact1.pk])
        response = self.url_get('unicef', url)

        responses = list(response.context['object_list'])
        self.assertEqual(len(responses), 2)
        # newest non-empty first
        self.assertEqual(responses, [self.pollrun2_r1, self.pollrun1_r1])
