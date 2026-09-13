import copy
import json
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_repository import import_apps
from store_publication import PublicationError
import test_store_publication as fixtures


@unittest.skipUnless(shutil.which('dpkg-deb'), 'real deb validation requires dpkg-deb')
class AppRepositoryTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.CompleteDebPublicationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        self.repo = self.root / 'repository'
        (self.repo / 'dist').mkdir(parents=True)
        shutil.copy2(fixture.source / fixture.filename, self.repo / 'dist' / fixture.filename)
        image = self.repo / 'docs/screenshots/home.png'
        image.parent.mkdir(parents=True)
        # Valid minimal 1x1 PNG fixture, not a product screenshot.
        import base64
        image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
        metadata = copy.deepcopy(fixture.metadata)
        metadata.pop('filename'); metadata.pop('sha256')
        self.data = {'schemaVersion': 1, 'repository': 'example/test', 'distribution': 'complete-deb',
                     'application': metadata, 'release': {'file': 'dist/' + fixture.filename, 'version': '1.0.0-1', 'sha256': fixture.metadata['sha256']},
                     'screenshots': [{'path': 'docs/screenshots/home.png', 'caption': 'Fixture'}]}
        self.output = self.root / 'submissions'
        self.save()

    def save(self):
        (self.repo / 'app.json').write_text(json.dumps(self.data))

    def test_real_package_import_keeps_payload_and_discoverable_metadata(self):
        entries = import_apps([self.repo], self.output)
        self.assertEqual(entries[0]['sourceRepository'], 'example/test')
        self.assertEqual(entries[0]['screenshots'], self.data['screenshots'])
        self.assertEqual(entries[0]['currentVersion'], '1.0.0-1')
        package = Path(self.data['release']['file'])
        self.assertEqual((self.repo / package).read_bytes(), (self.output / package.name).read_bytes())

    def test_bad_hash_rejects_everything_and_leaves_no_partial_source(self):
        self.data['release']['sha256'] = '0' * 64; self.save()
        with self.assertRaisesRegex(PublicationError, 'SHA-256'):
            import_apps([self.repo], self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.typix-import-*')))

    def test_metadata_conflict_and_wrapper_distribution_rejected(self):
        for change in [{'version': '9.0.0'}, {'file': '../outside.deb'}, {'file': ''}]:
            with self.subTest(change=change):
                original = copy.deepcopy(self.data)
                self.data['release'].update(change); self.save()
                with self.assertRaises(PublicationError): import_apps([self.repo], self.output)
                self.data = original
        self.data['distribution'] = 'entry-only'; self.save()
        with self.assertRaises(PublicationError): import_apps([self.repo], self.output)

    def test_symlink_escape_in_screenshot_ancestor_is_rejected(self):
        shutil.move(self.repo / 'docs', self.root / 'outside')
        (self.repo / 'docs').symlink_to(self.root / 'outside', target_is_directory=True)
        with self.assertRaisesRegex(PublicationError, 'symlink'):
            import_apps([self.repo], self.output)

    def test_duplicate_repo_and_existing_source_rejected(self):
        with self.assertRaisesRegex(PublicationError, 'Duplicate'):
            import_apps([self.repo, self.repo], self.output)
        self.output.mkdir(); (self.output / 'catalog.json').write_text('existing signed bytes')
        with self.assertRaisesRegex(PublicationError, 'never overwritten'):
            import_apps([self.repo], self.output)
        self.assertEqual((self.output / 'catalog.json').read_text(), 'existing signed bytes')

    def test_screenshot_format_and_traversal_rejected(self):
        self.data['screenshots'][0]['path'] = 'docs/screenshots/../home.png'; self.save()
        with self.assertRaises(PublicationError): import_apps([self.repo], self.output)
        self.data['screenshots'][0]['path'] = 'docs/screenshots/home.png'; self.save()
        (self.repo / 'docs/screenshots/home.png').write_text('not an image')
        with self.assertRaisesRegex(PublicationError, 'signature'):
            import_apps([self.repo], self.output)
