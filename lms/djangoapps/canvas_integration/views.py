from django.contrib.auth.models import User
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from opaque_keys.edx.locator import CourseLocator

from canvas_integration.client import CanvasClient
from courseware.courses import get_course_by_id
from canvas_integration import api
from lms.djangoapps.instructor.views.api import require_course_permission
from lms.djangoapps.instructor import permissions
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
@require_course_permission(permissions.EDIT_COURSE_ACCESS)
def list_canvas_enrollments(request, course_id):
    """
    Fetch enrollees for a course in canvas and list them
    """
    course_key = CourseLocator.from_string(course_id)
    course = get_course_by_id(course_key)
    if not course.canvas_course_id:
        # TODO: better exception class?
        raise Exception("No canvas_course_id set for course {}".format(course_id))

    client = CanvasClient(canvas_course_id=course.canvas_course_id)
    # WARNING: this will block the web thread
    enrollment_dict = client.list_canvas_enrollments()

    results = [
        {"email": email, **_get_edx_enrollment_data(email, course_key)} for email in sorted(enrollment_dict.keys())
    ]
    return JsonResponse(results)


@require_POST
@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.EDIT_COURSE_ACCESS)
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


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.EDIT_COURSE_ACCESS)
def list_canvas_assignments(request, course_id):
    """List Canvas assignments"""
    course_key = CourseLocator.from_string(course_id)
    course = get_course_by_id(course_key)
    client = CanvasClient(canvas_course_id=course.canvas_course_id)
    if not course.canvas_course_id:
        # TODO: better exception class?
        raise Exception("No canvas_course_id set for course {}".format(course_id))
    return JsonResponse(client.list_canvas_assignments())


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.EDIT_COURSE_ACCESS)
def list_canvas_grades(request, course_id):
    """List grades"""
    assignment_id = int(request.GET.get("assignment_id"))
    course_key = CourseLocator.from_string(course_id)
    course = get_course_by_id(course_key)
    client = CanvasClient(canvas_course_id=course.canvas_course_id)
    if not course.canvas_course_id:
        # TODO: better exception class?
        raise Exception("No canvas_course_id set for course {}".format(course_id))
    return JsonResponse(client.list_canvas_grades(assignment_id=assignment_id))


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.EDIT_COURSE_ACCESS)
def push_edx_grades(request, course_id):
    """Push user grades for all graded items in edX to Canvas"""
    course_key = CourseLocator.from_string(course_id)
    course = get_course_by_id(course_key)
    if not course.canvas_course_id:
        # TODO: better exception class?
        raise Exception("No canvas_course_id set for course {}".format(course_id))
    assignment_grades_updated, created_assignments = api.push_edx_grades_to_canvas(
        course=course
    )

    results = {}
    if assignment_grades_updated:
        grade_update_results = {}
        for subsection_block, grade_update_response in assignment_grades_updated.items():
            if grade_update_response.ok:
                message = "updated"
            else:
                message = {"error": grade_update_response.status_code}
            grade_update_results[subsection_block.display_name] = message
        results["grades"] = grade_update_results
    if created_assignments:
        created_assignment_results = {}
        for subsection_block, new_assignment_response in created_assignments.items():
            if new_assignment_response.ok:
                message = "created"
            else:
                message = {"error": new_assignment_response.status_code}
            created_assignment_results[subsection_block.display_name] = message
        results["assignments"] = created_assignment_results
    return JsonResponse(results)
