"""
Script for importing courseware from XML format
"""
from optparse import make_option

from django.core.management.base import BaseCommand, CommandError

from django_comment_common.utils import are_permissions_roles_seeded, seed_permissions_roles
from lms.djangoapps.dashboard.git_import import DEFAULT_COURSE_CODE_LIB_FILENAME
from xmodule.contentstore.django import contentstore
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.xml_importer import import_course_from_xml


class Command(BaseCommand):
    """
    Import the specified data directory into the default ModuleStore
    """
    help = 'Import the specified data directory into the default ModuleStore'

    option_list = BaseCommand.option_list + (
        make_option('--nostatic',
                    action='store_true',
                    help='Skip import of static content'),
        make_option('--nocodelib',
                    action='store_true',
                    help=(
                        'Skip import of custom code library if it exists'
                        '(NOTE: If static content is imported, the code library will also '
                        'be imported and this flag will be ignored)'
                    )),
        make_option('--code-lib-filename',
                    default=DEFAULT_COURSE_CODE_LIB_FILENAME,
                    help='Filename of the course code library (if it exists)'
                    ),
    )

    def handle(self, *args, **options):
        "Execute the command"
        if len(args) == 0:
            raise CommandError("import requires at least one argument: <data directory> [--nostatic] [<course dir>...]")

        data_dir = args[0]
        do_import_static = not options.get('nostatic', False)
        do_import_code_lib = not options.get('nocodelib', False)
        course_code_lib_filename = options.get('code_lib_filename')
        if len(args) > 1:
            source_dirs = args[1:]
        else:
            source_dirs = None
        self.stdout.write("Importing.  Data_dir={data}, source_dirs={courses}\n".format(
            data=data_dir,
            courses=source_dirs,
        ))
        mstore = modulestore()

        course_items = import_course_from_xml(
            mstore, ModuleStoreEnum.UserID.mgmt_command, data_dir, source_dirs, load_error_modules=False,
            static_content_store=contentstore(), verbose=True,
            do_import_static=do_import_static, do_import_code_lib=do_import_code_lib,
            create_if_not_present=True,
            python_lib_filename=course_code_lib_filename,
        )

        for course in course_items:
            course_id = course.id
            if not are_permissions_roles_seeded(course_id):
                self.stdout.write('Seeding forum roles for course {0}\n'.format(course_id))
                seed_permissions_roles(course_id)
