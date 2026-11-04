"""Actions preparation contracts, using real signed files and fake large images."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import build_carrier_pack as build
import carrier_payloads as payloads
import carrier_store as store

ROOT = Path(__file__).resolve().parent
IDENTITY = {'product': 'iPhone19,2', 'hardware': 'V63AP', 'version': '27.2', 'build': '24B119'}
URL = 'https://updates.cdn-apple.com/fixture.ipsw'


class RequestContracts(unittest.TestCase):
    def test_validate_only_needs_no_phone_network_or_mac(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / 'request.json'
            request.write_text(json.dumps(IDENTITY))
            output = io.StringIO()
            with patch.object(build, 'RemoteFirmware', side_effect=AssertionError('network')), \
                 patch.object(build, 'build_pack', side_effect=AssertionError('preparation')), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(build.main(['--request', str(request), '--validate-only']), 0)
            self.assertEqual(json.loads(output.getvalue()), {**IDENTITY, 'hardware': 'v63ap'})

    def test_rejects_private_identifiers_unknown_fields_and_unsafe_urls(self):
        for extra in ({'iccid': 'private'}, {'udid': 'private'}, {'imsi': 'private'},
                      {'firmware_url': 'https://example.com/a.ipsw'},
                      {'firmware_url': URL + '?token=secret'},
                      {'firmware_url': URL + '#fragment'}, {'version': '26.7'},
                      {'hardware': '../../escape'}, {'build': '$(echo injected)'},
                      {'product': None}, {'version': 27.2}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                build.request_identity({**IDENTITY, **extra})

    def test_duplicate_and_oversized_requests_stop_before_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / 'request.json'
            for content in ('{"product":"iPhone19,2","product":"iPhone12,1"}', ' ' * 16385):
                request.write_text(content)
                with self.assertRaises(ValueError):
                    build.read_request(request)


class PackPreparationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = SimpleNamespace(**IDENTITY)
        entries, objects = payloads.load_bundled(ROOT)
        entry = entries[store.entry_key(payloads.identity(cls.state))]
        cls.manifest, cls.bundles = payloads.validate_entry(cls.state, entry, objects)

    @contextlib.contextmanager
    def pipeline(self, *, manifest=None, bundles=None, mount_failure=False):
        manifest_bytes = self.manifest if manifest is None else manifest
        files = self.bundles if bundles is None else bundles
        events = []
        class Firmware:
            def __init__(self, url):
                events.append(('open', url))
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def manifest(self):
                return manifest_bytes
            def download_image(self, name, path):
                events.append(('download', path))
                path.write_bytes(b'encrypted fixture')
                return {'bytes': 17, 'sha256': store.digest(b'encrypted fixture'), 'crc32': 'fixture'}
        def decrypt(args):
            Path(args[3]).with_suffix('').write_bytes(b'decrypted fixture')
        def extract(image, work, *, build=None):
            self.assertEqual(image.read_bytes(), b'decrypted fixture')
            self.assertFalse(image.with_suffix('.dmg.aea').exists())
            if mount_failure:
                (work / 'mount-state-uncertain.txt').write_text('fixture')
                raise ValueError('simulated uncertain mount')
            return files
        with patch.object(build.platform, 'system', return_value='Darwin'), \
             patch.object(build.shutil, 'disk_usage', return_value=SimpleNamespace(free=40 * 1024**3)), \
             patch.object(build, 'RemoteFirmware', Firmware), \
             patch.object(payloads, 'ensure_ipsw', return_value=Path('/fixture/ipsw')), \
             patch.object(payloads, 'command', side_effect=decrypt), \
             patch.object(payloads, 'extract_macos', side_effect=extract), \
             patch.object(payloads, 'firmware_url', return_value=URL) as lookup, \
             patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': '', 'CARRIER_SELECTOR_HOME': '/fixture/original-home'}), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield events, lookup
            self.assertEqual(os.environ['CARRIER_SELECTOR_HOME'], '/fixture/original-home')

    def test_fresh_download_publishes_only_both_signed_bundles_and_receipts(self):
        # The identity is already in the included pack, but this builder must fetch it.
        with tempfile.TemporaryDirectory() as tmp, self.pipeline() as (events, lookup):
            output = Path(tmp) / 'result'
            report = build.build_pack({**IDENTITY, 'firmware_url': URL}, output)
            lookup.assert_not_called()
            self.assertEqual([event for event, _ in events], ['open', 'download'])
            self.assertEqual(list(Path(tmp).iterdir()), [output])
            self.assertEqual({p.name for p in output.iterdir()},
                             {report['pack']['file'], 'provenance.json', 'SHA256SUMS.txt'})
            self.assertEqual(json.loads((output / 'provenance.json').read_text()), report)
            self.assertEqual(report['device'], payloads.identity(self.state))
            self.assertEqual(report['filesystem_download']['bytes'], 17)
            self.assertEqual(set(report['bundles']), {'mvno', 'wholesale'})
            for line in (output / 'SHA256SUMS.txt').read_text().splitlines():
                digest, name = line.split('  ')
                self.assertEqual(store.digest((output / name).read_bytes()), digest)
            entries, objects = store.read(output / report['pack']['file'])
            self.assertEqual(len(entries), 1)
            entry = next(iter(entries.values()))
            self.assertEqual(payloads.validate_entry(self.state, entry, objects)[1], self.bundles)
            # The regular importer accepts the exact same artifact on any host.
            with patch.dict(os.environ, {'CARRIER_SELECTOR_HOME': str(Path(tmp) / 'imported')}):
                payloads.import_pack(tmp, self.state, output / report['pack']['file'])
                self.assertEqual(payloads.read_cached(tmp, self.state, 'mvno'), self.bundles['mvno'])
                with self.assertRaisesRegex(ValueError, 'exact model'):
                    payloads.import_pack(tmp, SimpleNamespace(**{**IDENTITY, 'build': '24B115'}),
                                         output / report['pack']['file'])

    def test_exact_lookup_when_no_explicit_url(self):
        with tempfile.TemporaryDirectory() as tmp, self.pipeline() as (events, lookup):
            build.build_pack(IDENTITY, Path(tmp) / 'result')
            lookup.assert_called_once()
            self.assertEqual(events[0], ('open', URL))

    def test_wrong_manifest_stops_before_large_download_and_leaves_no_artifact(self):
        changed = plistlib.loads(self.manifest)
        changed['ProductBuildVersion'] = '24B115'
        with tempfile.TemporaryDirectory() as tmp, self.pipeline(manifest=plistlib.dumps(changed)) as (events, _):
            with self.assertRaisesRegex(ValueError, 'exact iOS'):
                build.build_pack(IDENTITY, Path(tmp) / 'result')
            self.assertEqual([event for event, _ in events], ['open'])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_bad_signature_and_mismatched_pair_never_publish(self):
        corrupted = copy.deepcopy(self.bundles)
        corrupted['mvno']['carrier.plist'] += b'changed'
        mismatched = {**self.bundles, 'mvno': self.bundles['wholesale']}
        for bundles in (corrupted, mismatched):
            with self.subTest(bundles=bundles is corrupted), tempfile.TemporaryDirectory() as tmp, \
                 self.pipeline(bundles=bundles):
                with self.assertRaises(ValueError):
                    build.build_pack(IDENTITY, Path(tmp) / 'result')
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_unconfirmed_mount_retains_backing_image_without_publishing(self):
        with tempfile.TemporaryDirectory() as tmp, self.pipeline(mount_failure=True) as (events, _):
            output = Path(tmp) / 'result'
            with self.assertRaisesRegex(ValueError, 'uncertain mount'):
                build.build_pack(IDENTITY, output)
            self.assertFalse(output.exists())
            work = events[1][1].parent
            self.assertTrue((work / 'mount-state-uncertain.txt').exists())
            self.assertEqual(len(list(work.glob('*.dmg'))), 1)

    def test_existing_output_is_untouched_without_fetch(self):
        with tempfile.TemporaryDirectory() as tmp, self.pipeline() as (events, _):
            output = Path(tmp) / 'result'
            output.mkdir()
            (output / 'keep').write_text('original')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                build.build_pack(IDENTITY, output)
            self.assertEqual(events, [])
            self.assertEqual((output / 'keep').read_text(), 'original')


if __name__ == '__main__':
    unittest.main()
