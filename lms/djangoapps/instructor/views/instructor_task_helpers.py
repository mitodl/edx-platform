"""
A collection of helper utility functions for working with instructor
tasks.
"""
import json
import logging
import requests

from itertools import ifilter
from django.conf import settings
from django.utils.translation import ugettext as _
from django.utils.translation import ungettext

from bulk_email.models import CourseEmail
from lms.djangoapps.instructor_task.views import get_task_completion_info
from lms.djangoapps.grades.new.course_grade_factory import CourseGradeFactory
from student.models import CourseEnrollment
from util.date_utils import get_default_time_display

log = logging.getLogger(__name__)


def email_error_information():
    """
    Returns email information marked as None, used in event email
    cannot be loaded
    """
    expected_info = [
        'created',
        'sent_to',
        'email',
        'number_sent',
        'requester',
    ]
    return {info: None for info in expected_info}


def extract_email_features(email_task):
    """
    From the given task, extract email content information

    Expects that the given task has the following attributes:
    * task_input (dict containing email_id)
    * task_output (optional, dict containing total emails sent)
    * requester, the user who executed the task

    With this information, gets the corresponding email object from the
    bulk emails table, and loads up a dict containing the following:
    * created, the time the email was sent displayed in default time display
    * sent_to, the group the email was delivered to
    * email, dict containing the subject, id, and html_message of an email
    * number_sent, int number of emails sent
    * requester, the user who sent the emails
    If task_input cannot be loaded, then the email cannot be loaded
    and None is returned for these fields.
    """
    # Load the task input info to get email id
    try:
        task_input_information = json.loads(email_task.task_input)
    except ValueError:
        log.error("Could not parse task input as valid json; task input: %s", email_task.task_input)
        return email_error_information()

    email = CourseEmail.objects.get(id=task_input_information['email_id'])
    email_feature_dict = {
        'created': get_default_time_display(email.created),
        'sent_to': [target.long_display() for target in email.targets.all()],
        'requester': str(email_task.requester),
    }
    features = ['subject', 'html_message', 'id']
    email_info = {feature: unicode(getattr(email, feature)) for feature in features}

    # Pass along email as an object with the information we desire
    email_feature_dict['email'] = email_info

    # Translators: number sent refers to the number of emails sent
    number_sent = _('0 sent')
    if hasattr(email_task, 'task_output') and email_task.task_output is not None:
        try:
            task_output = json.loads(email_task.task_output)
        except ValueError:
            log.error("Could not parse task output as valid json; task output: %s", email_task.task_output)
        else:
            if 'succeeded' in task_output and task_output['succeeded'] > 0:
                num_emails = task_output['succeeded']
                number_sent = ungettext(
                    "{num_emails} sent",
                    "{num_emails} sent",
                    num_emails
                ).format(num_emails=num_emails)

            if 'failed' in task_output and task_output['failed'] > 0:
                num_emails = task_output['failed']
                number_sent += ", "
                number_sent += ungettext(
                    "{num_emails} failed",
                    "{num_emails} failed",
                    num_emails
                ).format(num_emails=num_emails)

    email_feature_dict['number_sent'] = number_sent
    return email_feature_dict


def extract_task_features(task):
    """
    Convert task to dict for json rendering.
    Expects tasks have the following features:
    * task_type (str, type of task)
    * task_input (dict, input(s) to the task)
    * task_id (str, celery id of the task)
    * requester (str, username who submitted the task)
    * task_state (str, state of task eg PROGRESS, COMPLETED)
    * created (datetime, when the task was completed)
    * task_output (optional)
    """
    # Pull out information from the task
    features = ['task_type', 'task_input', 'task_id', 'requester', 'task_state']
    task_feature_dict = {feature: str(getattr(task, feature)) for feature in features}
    # Some information (created, duration, status, task message) require additional formatting
    task_feature_dict['created'] = task.created.isoformat()

    # Get duration info, if known
    duration_sec = 'unknown'
    if hasattr(task, 'task_output') and task.task_output is not None:
        try:
            task_output = json.loads(task.task_output)
        except ValueError:
            log.error("Could not parse task output as valid json; task output: %s", task.task_output)
        else:
            if 'duration_ms' in task_output:
                duration_sec = int(task_output['duration_ms'] / 1000.0)
    task_feature_dict['duration_sec'] = duration_sec

    # Get progress status message & success information
    success, task_message = get_task_completion_info(task)
    status = _("Complete") if success else _("Incomplete")
    task_feature_dict['status'] = status
    task_feature_dict['task_message'] = task_message

    return task_feature_dict

def get_assignment_grade_datatable(course, assignment_name, task_progress=None):
    """
    Returns a datatable of students' grades for an assignment in the given course
    """
    if not assignment_name:
        return _("No assignment name given"), {}

    row_data = []
    current_step = {'step': 'Calculating Grades'}
    student_counter = 0
    enrolled_students = CourseEnrollment.objects.users_enrolled_in(course.id)
    total_enrolled_students = enrolled_students.count()

    for student, course_grade, error in CourseGradeFactory().iter(users=enrolled_students, course=course):
        # Periodically update task status (this is a cache write)
        student_counter += 1
        if task_progress is not None:
            task_progress.update_task_state(extra_meta=current_step)
            task_progress.attempted += 1

        log.info(
            u'%s, Current step: %s, Grade calculation in-progress for students: %s/%s',
            assignment_name,
            current_step,
            student_counter,
            total_enrolled_students
        )

        if course_grade and not error:
            matching_assignment_grade = next(
                ifilter(
                    lambda grade_section: grade_section['label'] == assignment_name,
                    course_grade.summary['section_breakdown']
                ), {}
            )
            row_data.append([student.email, matching_assignment_grade.get('percent', 0)])
            if task_progress is not None:
                task_progress.succeeded += 1
        else:
            if task_progress is not None:
                task_progress.failed += 1

    if task_progress is not None:
        task_progress.succeeded = student_counter
        task_progress.skipped = task_progress.total - task_progress.attempted
        current_step = {'step': 'Calculated Grades for {} students'.format(student_counter)}
        task_progress.update_task_state(extra_meta=current_step)

    datatable = dict(
        header=[_('External email'), assignment_name],
        data=row_data,
        title=_('Grades for assignment "{name}"').format(name=assignment_name)
    )
    return None, datatable


def do_remote_gradebook(email, course, action, files=None, **kwargs):
    """
    Perform remote gradebook action. Returns error message, response dict.
    """
    rg_url = settings.REMOTE_GRADEBOOK.get('URL')
    rg_user = settings.REMOTE_GRADEBOOK_USER
    rg_password = settings.REMOTE_GRADEBOOK_PASSWORD
    if not rg_url:
        error_msg = _("Missing required remote gradebook env setting: ") + "REMOTE_GRADEBOOK['URL']"
        return error_msg, {}
    elif not rg_user or not rg_password:
        error_msg = _(
            "Missing required remote gradebook auth settings: " +
            "REMOTE_GRADEBOOK_USER, REMOTE_GRADEBOOK_PASSWORD"
        )
        return error_msg, {}

    rg_course_settings = course.remote_gradebook or {}
    rg_name = rg_course_settings.get('name') or settings.REMOTE_GRADEBOOK.get('DEFAULT_NAME')
    if not rg_name:
        error_msg = _("No gradebook name defined in course remote_gradebook metadata and no default name set")
        return error_msg, {}

    data = dict(submit=action, gradebook=rg_name, user=email, **kwargs)
    resp = requests.post(
        rg_url,
        auth=(rg_user, rg_password),
        data=data,
        files=files,
        verify=False
    )
    if not resp.ok:
        error_header = _("Error communicating with gradebook server at {url}").format(url=rg_url)
        return '<p>{error_header}</p>{content}'.format(error_header=error_header, content=resp.content), {}
    return None, json.loads(resp.content)


def do_remote_gradebook_datatable(user, course, action, files=None, **kwargs):
    """
    Perform remote gradebook action that returns a datatable. Returns error message, datatable dict.
    """
    error_message, response_json = do_remote_gradebook(user.email, course, action, files=files, **kwargs)
    if error_message:
        return error_message, {}
    response_data = response_json.get('data')  # a list of dicts
    if not response_data or response_data == [{}]:
        return _("Remote gradebook returned no results for this action ({}).").format(action), {}
    datatable = dict(
        header=response_data[0].keys(),
        data=[x.values() for x in response_data],
        retdata=response_data,
    )
    return None, datatable
