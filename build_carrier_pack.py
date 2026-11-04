#!/usr/bin/env python3
"""Prepare a public, exact-firmware carrier pair on macOS, Windows, or Linux.

No phone connection, SIM identifiers, install, restore, or backup is involved.
Large images are temporary; only a validated carrier pack and receipts leave
the working directory. The installed CLI imports this pack with setup-device.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import sys
import tempfile
from types import SimpleNamespace
import urllib.parse

import carrier_payloads as payloads
import carrier_store as store
from carrier_remote import RemoteFirmware


def request_identity(raw):
    fields = {'product', 'hardware', 'version', 'build'}
    payloads.ensure(isinstance(raw, dict) and fields <= set(raw)
                    and set(raw) <= fields | {'firmware_url'},
                    'Provide only product, hardware, version, build, and optional firmware_url; no device/SIM identifiers.')
    payloads.ensure(all(isinstance(v, str) for v in raw.values()), 'Request values must be strings.')
    state = SimpleNamespace(**{k: raw[k].strip() for k in fields})
    result = payloads.identity(state)
    url = raw.get('firmware_url', '').strip()
    if url:
        payloads.apple_url(url)
        payloads.ensure(not urllib.parse.urlsplit(url).query,
                        'Use a public Apple IPSW URL without query parameters.')
        result['firmware_url'] = url
    return result


def read_request(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            payloads.ensure(key not in result, 'Duplicate request field.')
            result[key] = value
        return result
    with Path(path).open('rb') as source:
        data = source.read(16385)
    payloads.ensure(len(data) <= 16384, 'Firmware request exceeds 16 KiB.')
    return request_identity(json.loads(data, object_pairs_hook=unique))


def build_pack(request, output):
    request = request_identity(request)
    state = SimpleNamespace(**{k: request[k] for k in ('product', 'hardware', 'version', 'build')})
    payloads.ensure(platform.system() in {'Darwin', 'Windows', 'Linux'}, 'Unsupported firmware preparation host.')
    output = Path(output).expanduser().absolute()
    payloads.ensure(not output.exists() and not output.is_symlink(), 'Output directory already exists; it will not be overwritten.')
    output.parent.mkdir(parents=True, exist_ok=True)
    payloads.ensure(shutil.disk_usage(output.parent).free >= 25 * 1024**3,
                    'At least 25 GiB free temporary space is required; larger images may need more.')
    work = Path(tempfile.mkdtemp(prefix='.lightspeed-firmware-', dir=output.parent))
    previous_home = os.environ.get('CARRIER_SELECTOR_HOME')
    os.environ['CARRIER_SELECTOR_HOME'] = str(work)
    try:
        # A fresh isolated directory deliberately ignores the included/local
        # pack. The requested firmware is actually fetched on every build.
        url = request.get('firmware_url') or payloads.firmware_url(state)
        url = request_identity({**request, 'firmware_url': url})['firmware_url']
        print('Preparing ' + state.product + ' / ' + state.hardware + ' / iOS ' + state.version + ' / ' + state.build, flush=True)
        tool = payloads.ensure_firmware_tools(work)
        with RemoteFirmware(url) as archive:
            manifest_bytes = archive.manifest()
            image_name = payloads.validate_manifest(plistlib.loads(manifest_bytes), state)
            image = work / image_name
            receipt = archive.download_image(image_name, image)
        if image.suffix == '.aea':
            encrypted = image
            payloads.command([tool, 'fw', 'aea', encrypted, '--output', work])
            image = encrypted.with_suffix('')
            payloads.ensure(image.is_file(), 'Decrypted firmware image was not produced.')
            encrypted.unlink()
        bundles = payloads.extract_files(image, work, work, build=state.build)
        payloads.save_payload(work, state, manifest_bytes, url, bundles)
        prepared = work / 'artifact'
        prepared.mkdir()
        name = 'LightSpeedSwap-' + state.product.replace(',', '-') + '-' + state.hardware + '-' + state.version + '-' + state.build + '.carrierpack'
        pack = prepared / name
        payloads.export_pack(work, state, pack)
        entries, objects = store.read(pack)
        entry = entries[store.entry_key(payloads.identity(state))]
        payloads.validate_entry(state, entry, objects)
        report = {
            'schema': 'lightspeedswap.carrier-pack-build.v1',
            'host': {'system': platform.system(), 'architecture': platform.machine(),
                     'extractor': 'hdiutil' if platform.system() == 'Darwin' else '7-Zip 26.05'},
            'device': payloads.identity(state),
            'source_url': url,
            'filesystem_image': image_name,
            'filesystem_download': receipt,
            'build_manifest_sha256': store.digest(manifest_bytes),
            'pack': {'file': name, 'bytes': pack.stat().st_size, 'sha256': store.digest(pack.read_bytes())},
            'bundles': {target: {'identifier': 'com.apple.' + payloads.NAMES[target],
                                 'version': entry['bundles'][target]['version'],
                                 'signature_groups': entry['bundles'][target]['signature_groups'],
                                 'file_sha256': entry['bundles'][target]['files']}
                        for target in payloads.NAMES},
            'preparation': 'Original files from this exact firmware; no device activation test.',
        }
        commit = os.environ.get('GITHUB_SHA', '')
        if re.fullmatch(r'[0-9a-f]{40}', commit):
            report['workflow_commit'] = commit
        (prepared / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        (prepared / 'SHA256SUMS.txt').write_text(''.join(
            store.digest((prepared / file).read_bytes()) + '  ' + file + '\n'
            for file in (name, 'provenance.json')), encoding='utf-8')
        payloads.ensure(not output.exists() and not output.is_symlink(), 'Output directory appeared during preparation; refusing to replace it.')
        prepared.rename(output)
        print('Verified both bundles. Artifact: ' + name + ' (' + str(report['pack']['bytes']) + ' bytes)', flush=True)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with Path(summary).open('a', encoding='utf-8') as target:
                target.write('## Verified LightSpeedSwap carrier pack\n\n'
                             + 'Identity: `' + state.product + ' / ' + state.hardware + ' / ' + state.version + ' / ' + state.build + '`\n\n'
                             + 'Download and unzip the carrier-pack artifact, then import:\n\n```sh\n'
                             + 'python3 LightSpeedSwap.pyz setup-device --bundle-pack ' + name + '\n```\n\n'
                             + 'Pack SHA-256: `' + report['pack']['sha256'] + '`\n\n'
                             + 'No firmware images or SIM identifiers are uploaded. Signature verification does not establish on-device activation.\n')
        return report
    finally:
        if previous_home is None:
            os.environ.pop('CARRIER_SELECTOR_HOME', None)
        else:
            os.environ['CARRIER_SELECTOR_HOME'] = previous_home
        if not os.path.ismount(work / 'mount') and not (work / 'mount-state-uncertain.txt').exists():
            shutil.rmtree(work)
        else:
            print('Mount cleanup is unconfirmed; backing image retained at ' + str(work), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, help='JSON containing public model/board/version/build only')
    for field in ('product', 'hardware', 'version', 'build', 'firmware-url'):
        parser.add_argument('--' + field)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--validate-only', action='store_true', help='Validate and print the normalized request without network access')
    args = parser.parse_args(argv)
    try:
        names = ('product', 'hardware', 'version', 'build', 'firmware_url')
        if args.request:
            payloads.ensure(not any(getattr(args, k) is not None for k in names), 'Choose a request file or explicit fields, not both.')
            request = read_request(args.request)
        else:
            request = request_identity({k: getattr(args, k) for k in names if getattr(args, k) is not None})
        if args.validate_only:
            print(json.dumps(request, indent=2))
            return 0
        payloads.ensure(args.output_dir is not None, '--output-dir is required for preparation.')
        build_pack(request, args.output_dir)
        return 0
    except (ValueError, OSError) as error:
        print('Stopped: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
