"""Module for rerun"""
import json

from django.core.exceptions import PermissionDenied

from cms.djangoapps.contentstore.tasks import rerun_course
from course_action_state.models import CourseRerunState
from student.auth import has_studio_write_access

from xmodule.modulestore.exceptions import DuplicateCourseError
from xmodule.modulestore import EdxJSONEncoder
from xmodule.modulestore.django import modulestore

from cms.djangoapps.contentstore.utils import add_instructor


def rerun(source_course_key, org, number, run, fields, user):
    """
    Clone a course from existing one.

    Args:
        source_course_key (CourseKey): Coursekey object
        org (str): New course Name of organization
        number (str): New course number
        run (str): New course run
        fields (dict): New course related fields
        user (django.contrib.auth.user): Django user object

    returns:
        destination_course_key (CourseKey): New course key
    """
    # verify user has access to the original course
    if not has_studio_write_access(user, source_course_key):
        raise PermissionDenied()

    # create destination course key
    store = modulestore()
    with store.default_store('split'):
        destination_course_key = store.make_course_key(org, number, run)

    # verify org course and run don't already exist
    if store.has_course(destination_course_key, ignore_case=True):
        raise DuplicateCourseError(source_course_key, destination_course_key)

    # Make sure user has instructor and staff access to the destination course
    # so the user can see the updated status for that course
    add_instructor(destination_course_key, user, user)

    # Mark the action as initiated
    CourseRerunState.objects.initiated(source_course_key, destination_course_key, user, fields['display_name'])

    # Clear the fields that must be reset for the rerun
    fields['advertised_start'] = None

    # Rerun the course as a new celery task
    json_fields = json.dumps(fields, cls=EdxJSONEncoder)
    rerun_course.delay(unicode(source_course_key), unicode(destination_course_key), user.id, json_fields)

    return destination_course_key
