# -*- coding: utf-8 -*-

import logging
import os

from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


def remove_media(files):
    """
        Delete file from media dir
    """
    for filename in files:
        logger.info('Removing %s', filename)
        default_storage.delete(filename)


def remove_empty_dirs(path=None):
    """
        Recursively delete empty directories; return True if everything was deleted.
    """

    if not path:
        path = settings.MEDIA_ROOT

    if not os.path.isdir(path):
        return False

    listdir = [os.path.join(path, filename) for filename in os.listdir(path)]

    if all(list(map(remove_empty_dirs, listdir))):
        logger.info('Removing empty dir %s', path)
        os.rmdir(path)
        return True
    else:
        return False
