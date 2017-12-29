# pylint: disable=missing-docstring,unused-argument

import functools
import crum

from django.http import HttpResponse, HttpResponseNotFound, HttpResponseServerError

from edxmako.shortcuts import render_to_response, render_to_string
from openedx.core.djangolib.js_utils import dump_js_escaped_json

__all__ = ['not_found', 'server_error', 'render_404', 'render_500']


def jsonable_error(status=500, message="The Studio servers encountered an error"):
    """
    A decorator to make an error view return an JSON-formatted message if
    it was requested via AJAX.
    """
    def outer(func):
        @functools.wraps(func)
        def inner(request, *args, **kwargs):
            if request.is_ajax():
                content = dump_js_escaped_json({"error": message})
                return HttpResponse(content, content_type="application/json",
                                    status=status)
            else:
                return func(request, *args, **kwargs)
        return inner
    return outer


def fix_crum_request(func):
    """
    A decorator that ensures that the 'crum' package (a middleware that stores and fetches the current request in
    thread-local storate) can correctly fetch the current request. Under certain conditions, the current request cannot
    be fetched by crum (e.g.: when HTTP errors are raised in our views via 'raise Http404', et. al.). This decorator
    manually sets the current request for crum if it cannot be fetched.
    """
    @functools.wraps(func)
    def wrapper(request, *args, **kwargs):
        if not crum.get_current_request():
            crum.set_current_request(request=request)
        return func(request, *args, **kwargs)
    return wrapper


@jsonable_error(404, "Resource not found")
def not_found(request):
    return render_to_response('error.html', {'error': '404'})


@jsonable_error(500, "The Studio servers encountered an error")
def server_error(request):
    return render_to_response('error.html', {'error': '500'})


@fix_crum_request
@jsonable_error(404, "Resource not found")
def render_404(request):
    return HttpResponseNotFound(render_to_string('404.html', {}, request=request))


@fix_crum_request
@jsonable_error(500, "The Studio servers encountered an error")
def render_500(request):
    return HttpResponseServerError(render_to_string('500.html', {}, request=request))
