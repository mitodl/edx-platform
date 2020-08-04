"""Tasks for canvas"""

import logging
import hashlib
from functools import partial

from celery import task
from django.conf import settings
from django.utils.translation import ugettext_noop

from canvas_integration import api

TASK_LOG = logging.getLogger('edx.celery.task')


@task
def sync_canvas_enrollments(course_key, canvas_course_id, unenroll_current):
    """
    Fetch enrollments from canvas and update

    Args:
        course_key (str): The edX course key
        canvas_course_id (int): The canvas course id
        unenroll_current (bool): If true, unenroll existing students TODO
    """
    api.sync_canvas_enrollments(
        course_key=course_key,
        canvas_course_id=canvas_course_id,
        unenroll_current=unenroll_current,
    )
