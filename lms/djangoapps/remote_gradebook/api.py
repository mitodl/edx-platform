"""
API functionality for the remote gradebook app
"""
from django.contrib.auth.models import User
from courseware.access import has_access
from grades.context import grading_context_for_course
from student.models import CourseEnrollmentAllowed, CourseEnrollment


def enroll_emails_in_course(emails, course_key):
    """
    Attempts to enroll all provided emails in a course. Emails without a corresponding
    user have a CourseEnrollmentAllowed object created for the course.
    """
    results = {}
    for email in emails:
        user = User.objects.filter(email=email).first()
        result = ''
        if not user:
            _, created = CourseEnrollmentAllowed.objects.get_or_create(
                email=email,
                course_id=course_key
            )
            if created:
                result = 'User does not exist - created course enrollment permission'
            else:
                result = 'User does not exist - enrollment is already allowed'
        elif not CourseEnrollment.is_enrolled(user, course_key):
            try:
                CourseEnrollment.enroll(user, course_key)
                result = 'Enrolled user in the course'
            except Exception as ex:  # pylint: disable=broad-except
                result = 'Failed to enroll - {}'.format(ex)
        else:
            result = 'User already enrolled'
        results[email] = result
    return results


def get_enrolled_non_staff_users(course):
    """
    Returns an iterable of non-staff enrolled users for a given course
    """
    return [
        user for user in CourseEnrollment.objects.users_enrolled_in(course.id)
        if not has_access(user, 'staff', course)
    ]


def unenroll_non_staff_users_in_course(course):
    """
    Unenrolls non-staff users in a course
    """
    results = {}
    for enrolled_user in get_enrolled_non_staff_users(course):
        has_staff_access = has_access(enrolled_user, 'staff', course)
        if not has_staff_access:
            CourseEnrollment.unenroll(enrolled_user, course.id)
            result = 'Unenrolled user from the course'
        else:
            result = 'No action taken (staff user)'
        results[enrolled_user.email] = result
    return results


def course_graded_items(course):
    grading_context = grading_context_for_course(course)
    for graded_item_type, graded_items in grading_context['all_graded_subsections_by_type'].items():
        for graded_item_index, graded_item in enumerate(graded_items, start=1):
            yield graded_item_type, graded_item, graded_item_index


def get_assignment_type_label(course, assignment_type):
    """
    Gets the label for an assignment based on its type and the grading policy of the course.
    Returns the short label if one exists, or returns the full assignment type as the label
    if (a) the grading policy doesn't cover this assignment type, or (b) the grading policy
    has a blank short label for this assignment type
    """
    try:
        matching_policy = next(
            grader for grader in course.grading_policy['GRADER']
            if grader['type'] == assignment_type
        )
        return matching_policy.get('short_label') or assignment_type
    except StopIteration:
        return assignment_type


def get_course_assignment_choices(course):
    """
    Gets values and display labels for all assignments in a course based on the assignment type and the
    grading policy of the course.
    E.g.: [["Hw 01", "[Hw 01] Homework 1"], ["Ex 01", "[Ex 01] First Exam"]]

    :return List of two-item lists representing a value and display label for each assignment in the course
    """
    graded_item_choices = []
    for graded_item_type, graded_item, graded_item_index in course_graded_items(course):
        label = get_assignment_type_label(course, graded_item_type)
        subsection_block = graded_item.get("subsection_block")
        display_name = (
            subsection_block.display_name if subsection_block else ""
        )
        assignment_shorthand = "{label} {index:02d}".format(label=label, index=graded_item_index)
        graded_item_choices.append(
            [
                assignment_shorthand,
                "[{shorthand}] {display_name}".format(
                    shorthand=assignment_shorthand,
                    display_name=display_name,
                )
            ]
        )
    return graded_item_choices
