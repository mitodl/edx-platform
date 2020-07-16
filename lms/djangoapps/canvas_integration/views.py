from bridgekeeper.rules import is_staff
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from opaque_keys.edx.locator import CourseLocator

from courseware.courses import get_course_by_id
from canvas_integration import api
from remote_gradebook.views import require_course_permission
from student.models import CourseEnrollment, CourseEnrollmentAllowed
from util.json_request import JsonResponse


def _get_edx_enrollment_data(email, course_key):
    """Helper function to look up some info regarding whether a user with a email address is enrolled in edx"""
    user = User.objects.filter(email=email).first()
    allowed = CourseEnrollmentAllowed.objects.filter(email=email, course_id=course_key).first()

    return {
        "exists_in_edx": bool(user),
        "enrolled_in_edx": bool(user and CourseEnrollment.is_enrolled(user, course_key)),
        "allowed_in_edx": bool(allowed),
    }


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(is_staff)
def list_canvas_enrollments(request, course_id):
    """
    Fetch enrollees for a course in canvas and list them
    """
    course_key = CourseLocator.from_string(course_id)
    course = get_course_by_id(course_key)
    if not course.canvas_course_id:
        # TODO: better exception class?
        raise Exception("No canvas_course_id set for course {}".format(course_id))

    # WARNING: this will block the web thread
    enrollments = api.list_canvas_enrollments(course.canvas_course_id)

    results = [
        {"email": email, **_get_edx_enrollment_data(email, course_key)} for email in sorted(enrollments)
    ]

    return JsonResponse(results)


@require_POST
@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(is_staff)
def add_canvas_enrollments(request, course_id):
    """
    Fetches enrollees for a course in canvas and enrolls those emails in the course in edX
    """
    unenroll_current = request.POST.get('unenroll_current', '').lower() == 'true'
    course_key = CourseLocator.from_string(course_id)
    course = get_course_by_id(course_key)
    if not course.canvas_course_id:
        # TODO: better exception class?
        raise Exception("No canvas_course_id set for course {}".format(course_id))
    api.sync_canvas_enrollments(
        course_key=course_id,
        canvas_course_id=course.canvas_course_id,
        unenroll_current=unenroll_current,
    )  # WARNING: this will block the web thread
    return JsonResponse({"status": "success"})
