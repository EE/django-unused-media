# -*- coding: utf-8 -*-

import re
import time

from django.core.files.storage import default_storage
from django.core.validators import EMPTY_VALUES

from .remove import remove_media
from .utils import get_file_fields


def get_used_media():
    """
        Get media which are still used in models
    """

    media = set()

    for field in get_file_fields():
        is_null = {
            '%s__isnull' % field.name: True,
        }
        is_empty = {
            '%s' % field.name: '',
        }

        for value in field.model._base_manager \
                .values_list(field.name, flat=True) \
                .exclude(**is_empty).exclude(**is_null):
            if value not in EMPTY_VALUES:
                media.add(value)

    return media


def get_all_media(exclude=None, minimum_file_age=None):
    """
        Get all media entries from storage
    """

    if not exclude:
        exclude = []

    initial_time = time.time()
    return _get_media_recursive(default_storage, '', exclude, minimum_file_age, initial_time)


def _get_media_recursive(storage, prefix, pathexclude, minimum_file_age, initial_time):
    directories, files = storage.listdir(prefix)
    media = set()

    for name in files:
        name = prefix + name
        for e in pathexclude:
            if re.match(r'^%s$' % re.escape(e).replace('\\*', '.*'), name):
                break
        else:
            media.add(name)

        if minimum_file_age:
            file_age = initial_time - storage.get_modified_time(name).timestamp()
            if file_age < minimum_file_age:
                media.remove(name)

    for directory in directories:
        directory = prefix + directory + '/'
        for e in pathexclude:
            if re.match(r'^%s$' % re.escape(e).replace('\\*', '.*'), directory):
                break
        else:
            media |= _get_media_recursive(storage, directory, pathexclude, minimum_file_age, initial_time)

    return media


def get_unused_media(exclude=None, minimum_file_age=None):
    """
        Get media which are not used in models
    """

    if not exclude:
        exclude = []

    all_media = get_all_media(exclude, minimum_file_age)
    used_media = get_used_media()

    return all_media - used_media


def remove_unused_media():
    """
        Remove unused media
    """
    remove_media(get_unused_media())
