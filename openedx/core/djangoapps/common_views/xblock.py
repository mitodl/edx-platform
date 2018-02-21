"""
Common views dedicated to rendering xblocks.
"""
from __future__ import absolute_import

import logging
import mimetypes

from django.conf import settings
from django.http import Http404, HttpResponse
from xblock.core import XBlock
from xblock.django.request import django_to_webob_request, webob_to_django_response
from xblock.exceptions import NoSuchHandlerError

from openedx.core.lib.xblock_utils import get_aside_from_xblock
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

log = logging.getLogger(__name__)


def xblock_resource(request, block_type, uri):  # pylint: disable=unused-argument
    """
    Return a package resource for the specified XBlock.
    """
    try:
        # Figure out what the XBlock class is from the block type, and
        # then open whatever resource has been requested.
        xblock_class = XBlock.load_class(block_type, select=settings.XBLOCK_SELECT_FUNCTION)
        content = xblock_class.open_local_resource(uri)
    except IOError:
        log.info('Failed to load xblock resource', exc_info=True)
        raise Http404
    except Exception:
        log.error('Failed to load xblock resource', exc_info=True)
        raise Http404

    mimetype, _ = mimetypes.guess_type(uri)
    return HttpResponse(content, content_type=mimetype)


def invoke_xblock_aside_handler(request, usage_key, handler, suffix=''):
    """
    Dispatches a request to a handler specified in an XBlockAside, then returns the response.
    """
    req = django_to_webob_request(request)
    try:
        descriptor = modulestore().get_item(usage_key.usage_key)
        aside_instance = get_aside_from_xblock(descriptor, usage_key.aside_type)
        asides = [aside_instance] if aside_instance else []
        resp = aside_instance.handle(handler, req, suffix)
    except ItemNotFoundError:
        log.warning(u'Unable to load xblock wrapped by aside [usage_key: %s]', usage_key.usage_key)
        raise Http404
    except NoSuchHandlerError:
        log.info(
            "XBlockAside %s attempted to access missing handler %r",
            descriptor,
            handler,
            exc_info=True
        )
        raise Http404

    # unintentional update to handle any side effects of handle call
    # could potentially be updating actual course data or simply caching its values
    modulestore().update_item(descriptor, request.user.id, asides=asides)

    return webob_to_django_response(resp)
