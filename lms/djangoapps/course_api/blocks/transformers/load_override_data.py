"""
Load Override Data Transformer
"""
from datetime import datetime
import json
from dateutil.parser import parse

from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer

from courseware.models import StudentFieldOverride


class OverrideDataTransformer(BlockStructureTransformer):
    """
    A transformer that load override data in xblock.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1
    requested_fields = [
        'start', 'display_name', 'type', 'due', 'graded', 'special_exam_info', 'format', 'grading_policy'
    ]

    def __init__(self, user):
        self.user = user

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class;
        same identifier used in setup.py.
        """
        return "load_override_data"

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this transformer's transform method.
        """
        # collect basic xblock fields
        block_structure.request_xblock_fields(*cls.requested_fields)

    def _get_override_query(self, course_key, block_key):
        """
        returns queryset containing override data.

        Args:
            course_key (CourseLocator): course locator object
            block_key (UsageKey): usage key of block
        """
        return StudentFieldOverride.objects.filter(
            course_id=course_key,
            location=block_key,
            student_id=self.user.id,
            field__in=self.requested_fields
        )

    def _load_override_data(self, course_key, block_key, block_structure):
        """
        loads override data of block

        Args:
            course_key (CourseLocator): course locator object
            block_key (UsageKey): usage key of block
            block_structure (BlockStructure): block structure class
        """
        query = self._get_override_query(course_key, block_key)
        for student_field_override in query:
            if student_field_override.field == "due" or student_field_override.field == "start":
                value = parse(json.loads(student_field_override.value))
            else:
                value = json.loads(student_field_override.value)
            field = student_field_override.field
            block_structure.override_xblock_field(
                block_key,
                field,
                value
            )

    def transform(self, usage_info, block_structure):
        """
        loads override data into blocks
        """
        for block_key in block_structure.topological_traversal():
            self._load_override_data(
                usage_info.course_key,
                block_key,
                block_structure
            )
