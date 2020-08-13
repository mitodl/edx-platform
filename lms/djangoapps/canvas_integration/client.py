import time
from urllib.parse import urljoin
import requests

from django.conf import settings

from canvas_integration.constants import DEFAULT_ASSIGNMENT_POINTS


class CanvasClient:
    def __init__(self, canvas_course_id):
        self.session = self.get_canvas_session()
        self.canvas_course_id = canvas_course_id

    @staticmethod
    def get_canvas_session():
        """
        Create a request session with the access token
        """
        session = requests.Session()
        session.headers.update({
            "Authorization": "Bearer {token}".format(token=settings.CANVAS_ACCESS_TOKEN)
        })
        return session

    def list_canvas_enrollments(self):
        """
        Fetch canvas enrollments. This may take a while, so don't run in the request thread.

        Args:
            canvas_course_id (int): The canvas id for the course

        Returns:
            dict: Email addresses mapped to canvas user ids for all enrolled users
        """
        email_id_map = {}
        url = urljoin(
            settings.CANVAS_BASE_URL,
            "/api/v1/courses/{course_id}/enrollments".format(course_id=self.canvas_course_id)
        )
        while url:
            resp = self.session.get(url)
            resp.raise_for_status()
            links = requests.utils.parse_header_links(resp.headers["link"])
            url = None
            for link in links:
                if link["rel"] == "next":
                    url = link["url"]
                    # TODO: what's an appropriate delay? Does edX have a standard for this?
                    time.sleep(0.2)

            email_id_map.update({
                enrollment["user"]["login_id"].lower(): enrollment["user"]["id"]
                for enrollment in resp.json()
            })
        return email_id_map

    def get_assignment_integration_ids(self):
        return {
            assignment.get("integration_id")
            for assignment in self.list_canvas_assignments()
        }

    def list_canvas_assignments(self):
        """
        List Canvas assignments
        """
        url = urljoin(settings.CANVAS_BASE_URL, "/api/v1/courses/{course_id}/assignments".format(
            course_id=self.canvas_course_id
        ))
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def get_assignments_by_int_id(self):
        assignments = self.list_canvas_assignments()
        return {
            assignment.get("integration_id"): assignment["id"]
            for assignment in assignments
            if assignment.get("integration_id") is not None
        }

    def list_canvas_grades(self, assignment_id):
        """
        List grades for a Canvas assignment

        Args:
            assignment_id (int): The canvas assignment id
        """
        url = urljoin(
            settings.CANVAS_BASE_URL,
            "/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions".format(
                course_id=self.canvas_course_id,
                assignment_id=assignment_id,
            )
        )
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def create_canvas_assignment(self, payload):
        return self.session.post(
            url=urljoin(
                settings.CANVAS_BASE_URL,
                "/api/v1/courses/{course_id}/assignments".format(course_id=self.canvas_course_id)
            ),
            json=payload,
        )

    def update_assignment_grades(self, canvas_assignment_id, payload):
        return self.session.post(
            url=urljoin(
                settings.CANVAS_BASE_URL,
                "/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/update_grades".format(
                    course_id=self.canvas_course_id,
                    assignment_id=canvas_assignment_id
                )
            ),
            data=payload,
        )


def create_assignment_payload(subsection_block):
    return {
        "assignment": {
            "name": subsection_block.display_name,
            "integration_id": str(subsection_block.location),
            "grading_type": "percent",
            "points_possible": DEFAULT_ASSIGNMENT_POINTS,
            "submission_types": ["online_upload"],
            "published": True,
        }
    }


def update_grade_payload_kv(user_id, grade_percent):
    return (
        "grade_data[{user_id}][posted_grade]".format(user_id=user_id),
        "{pct}%".format(pct=grade_percent * 100)
    )