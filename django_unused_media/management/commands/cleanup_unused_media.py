import logging
import os
import re
import time

from django.apps import apps
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.core.validators import EMPTY_VALUES
from django.db import models

logger = logging.getLogger(__name__)


class Command(BaseCommand):

    help = "Clean unused media files which have no reference in models"

    def add_arguments(self, parser):

        parser.add_argument('--noinput', '--no-input',
                            dest='interactive',
                            action='store_false',
                            default=True,
                            help='Do not ask confirmation')

        parser.add_argument('-e', '--exclude',
                            dest='exclude',
                            action='append',
                            default=[],
                            help='Exclude files by mask (only * is supported), can use multiple --exclude')

        parser.add_argument('--minimum-file-age',
                            dest='minimum_file_age',
                            default=60,
                            type=int,
                            help='Skip files younger this age (sec)')

        parser.add_argument('--remove-empty-dirs',
                            dest='remove_empty_dirs',
                            action='store_true',
                            default=False,
                            help='Remove empty dirs after files cleanup')

        parser.add_argument('-n', '--dry-run',
                            dest='dry_run',
                            action='store_true',
                            default=False,
                            help='Dry run without any affect on your data')

    def handle(self, *args, **options):
        self.verbosity = options['verbosity']

        unused_media = get_unused_media(
            exclude=options.get('exclude'),
            minimum_file_age=options.get('minimum_file_age'),
        )

        if not unused_media:
            self._report('Nothing to delete. Exit')
            return

        self._report('Files to remove:\n' + '\n'.join(sorted(unused_media)), verbosity=2)
        self._report('Total files will be removed: {}'.format(len(unused_media)))

        if options.get('dry_run'):
            self._report('Dry run. Exit.')
            return

        if options.get('interactive'):
            question = 'Are you sure you want to remove {} unused files? (y/N)'.format(len(unused_media))
            if input(question).upper() != 'Y':
                self._report('Interrupted by user. Exit.')
                return

        remove_media(unused_media)

        if options.get('remove_empty_dirs'):
            remove_empty_dirs()

        self._report('Done. Total files removed: {}'.format(len(unused_media)))

    def _report(self, message, verbosity=1):
        if self.verbosity >= verbosity:
            self.stdout.write(message)


def get_used_media():
    """
        Get media which are still used in models
    """

    media = set()
    total_refs = 0

    fields = get_file_fields()

    logger.info("Starting to scan %d fields for media references", len(fields))

    for field in fields:
        is_null = {
            '%s__isnull' % field.name: True,
        }
        is_empty = {
            '%s' % field.name: '',
        }

        field_values = field.model._base_manager \
            .values_list(field.name, flat=True) \
            .exclude(**is_empty).exclude(**is_null)

        field_refs = 0
        for value in field_values:
            if value not in EMPTY_VALUES:
                media.add(value)
                field_refs += 1
                total_refs += 1

        logger.info(
            "Scanning %s.%s.%s... got %d media refs, total media refs now is %d",
            field.model._meta.app_label, field.model._meta.model_name,
            field.name, field_refs, total_refs,
        )

    logger.info("Finished scanning. Found %d unique media references", len(media))

    return media


def get_all_media(exclude=None):
    """
        Get all media entries from storage
    """

    if not exclude:
        exclude = []

    logger.info("Starting to scan media storage for files")

    all_media = _get_media_recursive(default_storage, '', exclude)

    logger.info("Finished scanning storage. Found %d files", len(all_media))

    return all_media


REPORT_EVERY_N_FILES = 1000


def _get_media_recursive(storage, prefix, pathexclude):
    directories, files = storage.listdir(prefix)
    media = set()

    for name in files:
        name = prefix + name
        for e in pathexclude:
            if re.match(r'^%s$' % re.escape(e).replace('\\*', '.*'), name):
                break
        else:
            media.add(name)
            if len(media) % REPORT_EVERY_N_FILES == 0:
                logger.info("Prefix %s has %d flat files so far", prefix, len(media))

    for directory in directories:
        directory = prefix + directory + '/'
        for e in pathexclude:
            if re.match(r'^%s$' % re.escape(e).replace('\\*', '.*'), directory):
                break
        else:
            len_before = len(media)
            media |= _get_media_recursive(storage, directory, pathexclude)
            len_after = len(media)
            if len_after // REPORT_EVERY_N_FILES > len_before // REPORT_EVERY_N_FILES:
                logger.info("Prefix %s has %d flat and nested files so far", prefix, len_after)

    return media


def get_unused_media(exclude=None, minimum_file_age=None):
    """
        Get media which are not used in models
    """

    if not exclude:
        exclude = []

    all_media = get_all_media(exclude)
    # A file uploaded just before the references are read may belong to a row
    # whose transaction has not committed yet, so file ages count back from this moment.
    references_read_at = time.time()
    used_media = get_used_media()

    unused = all_media - used_media
    missing = used_media - all_media

    logger.info(
        "Analysis complete: %d total files, %d used files, %d unused files, %d missing files",
        len(all_media), len(used_media), len(unused), len(missing),
    )

    if minimum_file_age:
        # Only the unused files are checked:
        # on remote storage each check is a request of its own.
        logger.info("Checking age of %d unused files", len(unused))
        unused = {
            name for name in unused
            if references_read_at - default_storage.get_modified_time(name).timestamp() >= minimum_file_age
        }
        logger.info("%d unused files are at least %d s old", len(unused), minimum_file_age)

    return unused


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


def get_file_fields():
    """
        Get all fields which are inherited from FileField
    """

    # get models
    all_models = apps.get_models()

    # get fields
    fields = []

    for model in all_models:
        for field in model._meta.get_fields():
            if isinstance(field, models.FileField):
                fields.append(field)

    logger.info("Detected %d model fields that inherit from FileField", len(fields))

    return fields
