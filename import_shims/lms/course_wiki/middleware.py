from import_shims.warn import warn_deprecated_import

warn_deprecated_import('course_wiki.middleware', 'lms.djangoapps.course_wiki.middleware')

from lms.djangoapps.course_wiki.middleware import *
