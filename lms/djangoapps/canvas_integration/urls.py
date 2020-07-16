"""
Canvas Integration API endpoint urls.
"""

from django.conf.urls import url

from canvas_integration import views

urlpatterns = [
    url(r'^add_canvas_enrollments$',
        views.add_canvas_enrollments, name="add_canvas_enrollments"),
    url(r'^list_canvas_enrollments$',
        views.list_canvas_enrollments, name="list_canvas_enrollments"),
]
