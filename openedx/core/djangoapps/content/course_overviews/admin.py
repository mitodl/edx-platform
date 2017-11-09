"""
Django admin page for CourseOverviews, the basic metadata about a course that
is used in user dashboard queries and other places where you need info like
name, and start dates, but don't actually need to crawl into course content.
"""
import datetime
import uuid
from django.contrib import admin
from opaque_keys.edx.keys import CourseKey

from config_models.admin import ConfigurationModelAdmin
from .models import CourseOverview, CourseOverviewImageConfig, CourseOverviewImageSet
from cms.djangoapps.contentstore.rerun_util import rerun


def rerun_course(__, request, queryset):
    for data in queryset:
        source_course_key = data.id
        run = '{year}_{slug}'.format(
            year=datetime.datetime.today().year,
            slug=uuid.uuid4().hex[:4]
        )
        fields = {
            'display_name': data.display_name
        }
        print(type(source_course_key))
        rerun(
            CourseKey.from_string(source_course_key),
            source_course_key.org,
            source_course_key.course,
            run,
            fields,
            request.user
        )


rerun_course.short_description = "It rerun's the selected course(s)"


class CourseOverviewAdmin(admin.ModelAdmin):
    """
    Simple, read-only list/search view of Course Overviews.

    The detail view is broken because our primary key for this model are
    course keys, which can have a number of chars that break admin URLs.
    There's probably a way to make this work properly, but I don't have the
    time to investigate. I would normally disable the links by setting
    `list_display_links = None`, but that's not a valid value for that
    field in Django 1.4. So I'm left with creating a page where the detail
    view links are all broken for Split courses. Because I only created
    this page to manually test a hotfix, the list view works for this
    purpose, and that's all the yak I have time to shave today.
    """
    list_display = [
        'id',
        'display_name',
        'version',
        'enrollment_start',
        'enrollment_end',
        'created',
        'modified',
    ]

    search_fields = ['id', 'display_name']
    actions = [rerun_course]


class CourseOverviewImageConfigAdmin(ConfigurationModelAdmin):
    """
    Basic configuration for CourseOverview Image thumbnails.

    By default this is disabled. If you change the dimensions of the images with
    a new config after thumbnails have already been generated, you need to clear
    the entries in CourseOverviewImageSet manually for new entries to be
    created.
    """
    list_display = [
        'change_date',
        'changed_by',
        'enabled',
        'large_width',
        'large_height',
        'small_width',
        'small_height'
    ]

    def get_list_display(self, request):
        """
        Restore default list_display behavior.

        ConfigurationModelAdmin overrides this, but in a way that doesn't
        respect the ordering. This lets us customize it the usual Django admin
        way.
        """
        return self.list_display


class CourseOverviewImageSetAdmin(admin.ModelAdmin):
    """
    Thumbnail images associated with CourseOverviews. This should be used for
    debugging purposes only -- e.g. don't edit these values.
    """
    list_display = [
        'course_overview',
        'small_url',
        'large_url',
    ]
    search_fields = ['course_overview__id']
    readonly_fields = ['course_overview_id']
    fields = ('course_overview_id', 'small_url', 'large_url')


admin.site.register(CourseOverview, CourseOverviewAdmin)
admin.site.register(CourseOverviewImageConfig, CourseOverviewImageConfigAdmin)
admin.site.register(CourseOverviewImageSet, CourseOverviewImageSetAdmin)
