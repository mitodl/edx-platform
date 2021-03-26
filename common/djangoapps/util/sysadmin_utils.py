"""
Utilities for edx-sysadmin plugin.
"""

from django.conf import settings

from edx_django_utils.plugins.plugin_manager import PluginError, PluginManager
from openedx.core.djangoapps.plugins.constants import ProjectType


def is_edx_sysadmin_installed():
    """
    Checks if edx-sysadmin plugin is installed in the platform
    :return boolean: True if plugin is installed else False
    """
    try:
        if PluginManager.get_plugin("edx_sysadmin", ProjectType.LMS):
            return True
    except PluginError:
        return False


def user_has_access_to_sysadmin(user):
    """
    Checks if user has access to sysadmin panel or not
    :param user: User object of currently loggedin user
    :return boolean: True if user has access to syadmin else False
    """
    # TODO: Give access to Course Admins
    if user and user.is_staff:
        return True


def show_sysadmin_dashboard(user):
    """
    Checks if all the requirements for showing edx-sysadmin are fulfilled
    :return boolean: True if all requirements are fulfilled else False
    """
    return is_edx_sysadmin_installed() and user_has_access_to_sysadmin(user)
