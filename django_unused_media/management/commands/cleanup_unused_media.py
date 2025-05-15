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

    # verbosity
    # 0 means silent
    # 1 means normal output (default).
    # 2 means verbose output

    verbosity = 1

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

    def info(self, message):
        if self.verbosity > 0:
            self.stdout.write(message)

    def debug(self, message):
        if self.verbosity > 1:
            self.stdout.write(message)

    def _show_files_to_delete(self, unused_media):
        self.debug('Files to remove:')

        for f in unused_media:
            self.debug(f)

        self.info('Total files will be removed: {}'.format(len(unused_media)))

    def handle(self, *args, **options):

        if 'verbosity' in options:
            self.verbosity = options['verbosity']

        self._configure_logging()

        unused_media = get_unused_media(
            exclude=options.get('exclude'),
            minimum_file_age=options.get('minimum_file_age'),
        )

        if not unused_media:
            self.info('Nothing to delete. Exit')
            return

        if options.get('dry_run'):
            self._show_files_to_delete(unused_media)
            self.info('Dry run. Exit.')
            return

        if options.get('interactive'):
            self._show_files_to_delete(unused_media)

            # ask user

            question = 'Are you sure you want to remove {} unused files? (y/N)'.format(len(unused_media))

            if input(question).upper() != 'Y':
                self.info('Interrupted by user. Exit.')
                return

        remove_media(unused_media)

        if options.get('remove_empty_dirs'):
            remove_empty_dirs()

        self.info('Done. Total files removed: {}'.format(len(unused_media)))

    def _configure_logging(self):
        if self.verbosity == 0:
            level = logging.ERROR
        elif self.verbosity == 1:
            level = logging.INFO
        else:
            level = logging.DEBUG

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        # Ensure we have a console handler for immediate feedback
        if not root_logger.handlers:
            console_handler = logging.StreamHandler(self.stdout)
            console_handler.setLevel(level)
            formatter = logging.Formatter('%(message)s')  # Simple format for console output
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)

        # Set our module logger level too
        logger.setLevel(level)


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


def get_all_media(exclude=None, minimum_file_age=None):
    """
        Get all media entries from storage
    """

    if not exclude:
        exclude = []

    logger.info("Starting to scan media storage for files")

    initial_time = time.time()
    all_media = _get_media_recursive(default_storage, '', exclude, minimum_file_age, initial_time)

    logger.info("Finished scanning storage. Found %d files", len(all_media))

    return all_media


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

    unused = all_media - used_media

    logger.info(
        "Analysis complete: %d total files, %d used files, %d unused files",
        len(all_media), len(used_media), len(unused),
    )

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
