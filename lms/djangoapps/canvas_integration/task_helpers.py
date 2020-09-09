"""
Helper functions for canvas integration tasks
"""
from time import time

from canvas_integration import api
from courseware.courses import get_course_by_id
from instructor_task.tasks_helper.runner import TaskProgress


def sync_canvas_enrollments(_xmodule_instance_args, _entry_id, course_id, task_input, action_name):
    """Partial function to sync canvas enrollments"""
    start_time = time()
    num_reports = 1
    task_progress = TaskProgress(action_name, num_reports, start_time)
    api.sync_canvas_enrollments(
        course_key=task_input["course_key"],
        canvas_course_id=task_input["canvas_course_id"],
        unenroll_current=task_input["unenroll_current"],
    )
    # for simplicity, only one task update for now when everything is done
    return task_progress.update_task_state(extra_meta={
        "step": "Done"
    })


def push_edx_grades_to_canvas(_xmodule_instance_args, _entry_id, course_id, task_input, action_name):
    """Partial function to push edX grades to canvas"""
    start_time = time()
    num_reports = 1
    task_progress = TaskProgress(action_name, num_reports, start_time)
    course = get_course_by_id(course_id)
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

    # for simplicity, only one task update for now when everything is done
    return task_progress.update_task_state(extra_meta={
        "step": "Done",
        "results": results
    })
