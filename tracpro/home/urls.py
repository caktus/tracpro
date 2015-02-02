from __future__ import absolute_import, unicode_literals

from django.conf.urls import patterns, url
from .views import HomeView

urlpatterns = patterns('', url(r'^$', HomeView.as_view(), name='home.home'))
urlpatterns += patterns('', url(r'^home/(?P<region>\d+)/$', HomeView.as_view(), name='home.home_for'))
