"""
Management command to translate course content to a specified language.
"""
import logging
import deepl
import os
import tarfile
import shutil

from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Translate given course content to the specified language.
    """
    help = (
        "Translate course content to the specified language."
    )

    def add_arguments(self, parser):
        parser.add_argument('--translation-language', dest='translation_language',
                            help='Specify the language to translate the course content into.')
        parser.add_argument('--course-dir', dest='course_directory', help='Specify the course directory.')

    def handle(self, *args, **options):
        course_dir = options.get('course_directory')
        translation_language = options.get('translation_language')
        extract_dir = '/openedx/course_translations'
        target_dirs = ['about', 'course', 'chapter', 'html', 'info', 'problem', 'sequential', 'vertical', 'video']

        # Step 1: Extract tarball if needed
        if course_dir.endswith('.tar.gz') or course_dir.endswith('.tgz') or course_dir.endswith('.tar'):
            if not os.path.exists(extract_dir):
                os.makedirs(extract_dir)
            tarball_base = os.path.basename(course_dir)
            for ext in ['.tar.gz', '.tgz', '.tar']:
                if tarball_base.endswith(ext):
                    tarball_base = tarball_base[:-len(ext)]
                    break
            extracted_course_dir = os.path.join(extract_dir, tarball_base)
            if not os.path.exists(extracted_course_dir):
                with tarfile.open(course_dir, 'r:*') as tar:
                    tar.extractall(path=extracted_course_dir)
            source_dir = extracted_course_dir
        else:
            source_dir = course_dir

        # Step 2: Always copy to /openedx/course_translations/{translation_language}_{base_name}
        base_name = os.path.basename(source_dir)
        new_dir_name = f"{translation_language}_{base_name}"
        new_dir_path = os.path.join(extract_dir, new_dir_name)
        if os.path.exists(new_dir_path):
            shutil.rmtree(new_dir_path)
        shutil.copytree(source_dir, new_dir_path)
        print(f"Copied {source_dir} to {new_dir_path}")

        # Step 3: Traverse copied directory (including its parent) and print html/xml files
        billed_char_count = 0
        parent_dir = os.path.dirname(new_dir_path)
        for search_dir in [new_dir_path, parent_dir]:
            for file in os.listdir(search_dir):
                file_path = os.path.join(search_dir, file)
                if os.path.isfile(file_path) and (file.endswith('.html') or file.endswith('.xml')):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        print(f"--- Contents of {file_path} ---")
                        translated_content, billed_chars = self._translate_text(file, content, translation_language)
                        billed_char_count = billed_char_count + billed_chars

                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(translated_content)
                        print(f"--- Translated Contents of {file_path} ---")
                        print(translated_content)
                        # print(content)

            for dir_name in target_dirs:
                dir_path = os.path.join(search_dir, dir_name)
                if os.path.exists(dir_path) and os.path.isdir(dir_path):
                    for root, _, files in os.walk(dir_path):
                        for file in files:
                            if file.endswith('.html') or file.endswith('.xml'):
                                file_path = os.path.join(root, file)
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    content = f.read()
                                    print(f"--- Contents of {file_path} ---")
                                    translated_content, billed_chars = self._translate_text(file, content, translation_language, billed_char_count)
                                    billed_char_count = billed_char_count + billed_chars

                                with open(file_path, 'w', encoding='utf-8') as f:
                                    f.write(translated_content)
                                    print(f"--- Translated Contents of {file_path} ---")
                                    print(translated_content)
                                    # print(content)

    def _translate_text(self, file, text, target_language, billed_char_count):
        """
        Translate the given text to the target language using DeepL API.
        """
        deepl_client = deepl.DeepLClient(auth_key)
        result = deepl_client.translate_text(
            text,
            source_lang="EN-US",
            target_lang="AR",
            tag_handling=file.split('.')[-1],
            tag_handling_version='v2',
        )
        return result.text, result.billed_characters
