"""Import each phone's original carrier files from its exact Apple firmware.

Host-only operations. No device service, restore, backup, or installation.
macOS uses a read-only APFS mount; Windows/Linux use a pinned native APFS reader.
"""
import io
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request

from carrier_signature_audit import read_package, check_signatures
import carrier_store as store
from carrier_paths import data_root
from carrier_https import https_context

NAMES = {"mvno": "TMobile_MVNO_US", "wholesale": "TMobile_Wholesale_US"}
IPSW_VERSION = "3.1.741"
IPSW_DIGESTS = {
    "arm64": "546af036b81c957ac7ddc71098ef65d2a8efebb70c81cce65bac4b9499091e8a",
    "x86_64": "6f46730c3924ccce1dd5300d45ca5e24cb69651ee17c53290302510f23f29975",
}
IPSW_BINARY_DIGESTS = {
    "arm64": "c3ae23e24c817c2b4a24e9b908ec2be6f5cd544d91986f4f77ea23c3aaa0d9fc",
    "x86_64": "8275526c9e923778ca4cb0ed6f56f9c7a5faa3993557d6841b9352935742838c",
}
BUNDLED_SHA256 = "1e31982747014f56e55133d6bc7bdf45793642ea40d964dcd8d2256489fdb5d9"


def ensure(ok, text):
    if not ok:
        raise ValueError(text)


def version_tuple(value):
    ensure(isinstance(value, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,3}', value),
           'Unknown carrier version format; cannot establish a safe upgrade order.')
    parts = tuple(int(v) for v in value.split('.'))
    return parts + (0,) * (4 - len(parts))


def identity(state):
    ensure(all(isinstance(getattr(state, name, None), str) for name in
               ('product', 'hardware', 'version', 'build')), 'Device identity fields must be strings.')
    ensure(re.fullmatch(r"iPhone[0-9]+,[0-9]+", state.product), "An iPhone product identifier is required.")
    ensure(re.fullmatch(r"[A-Za-z0-9]{3,24}", state.hardware), "Missing or invalid hardware identifier.")
    ensure(re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", state.version), "Unrecognized iOS version.")
    ensure(int(state.version.split('.')[0]) >= 27, "This app requires iOS 27 or later.")
    ensure(re.fullmatch(r"[A-Za-z0-9]{4,24}", state.build), "Missing or invalid build identifier.")
    return {"product": state.product, "hardware": state.hardware.lower(),
            "version": state.version, "build": state.build}


def cache_path(root, state=None):
    if state is not None:
        identity(state)
    return data_root(root) / '.carrier-cache' / 'verified-payloads.zip'


def load_bundled(root):
    path = Path(root) / 'bundled.carrierpack'
    ensure(path.is_file() and not path.is_symlink(), 'The included bundle pack is missing.')
    ensure(path.stat().st_size <= store.MAX_CACHE_BYTES, 'The included bundle pack is too large.')
    ensure(store.digest(path.read_bytes()) == BUNDLED_SHA256, 'The included bundle pack changed.')
    return store.read(path)


def locate(root, state):
    key = store.entry_key(identity(state))
    cached = cache_path(root)
    if cached.exists():
        content = store.read(cached)
        if key in content[0]:
            return 'cached', content, key
    if (Path(root) / 'bundled.carrierpack').exists():
        content = load_bundled(root)
        if key in content[0]:
            return 'included', content, key
    raise MissingPayload('No verified payload for this model/build. Run: bash Run.command setup-device')


class MissingPayload(ValueError):
    pass


def validate_files(target, files, *, build=None):
    ensure(target in NAMES, 'Unknown carrier target.')
    ensure(isinstance(files, dict) and 'Info.plist' in files, 'Missing carrier Info.plist.')
    info = plistlib.loads(files['Info.plist'])
    ensure(isinstance(info, dict), 'Unsupported carrier Info.plist format.')
    ensure(info.get('CFBundleIdentifier') == 'com.apple.' + NAMES[target], "Wrong carrier bundle identity.")
    ensure(info.get('CFBundleDeviceFamily') == 'iPhone', "The bundle is not for iPhone.")
    ensure(re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,3}', str(info.get('CFBundleVersion', ''))),
           "Missing carrier bundle version.")
    signatures = check_signatures(files, build=build)
    ensure(all(v['signature_valid'] for v in signatures.values()), "An Apple carrier signature failed verification.")
    return info, signatures


def validate_entry(state, entry, objects):
    ensure(entry['device'] == identity(state), 'Payload identity does not match this phone.')
    manifest_bytes = objects[entry['manifest']]
    image = validate_manifest(plistlib.loads(manifest_bytes), state)
    ensure(entry['filesystem_image'] == image, 'Payload filesystem image mismatch.')
    bundles = {}
    board = state.hardware.lower().removesuffix('ap')
    for target in NAMES:
        metadata = entry['bundles'][target]
        files = {name: objects[sha] for name, sha in metadata['files'].items()}
        info, groups = validate_files(target, files, build=state.build)
        ensure(metadata['version'] == info['CFBundleVersion'] and metadata['signature_groups'] == sorted(groups),
               'Bundle metadata does not match its signed files.')
        overrides = set(groups) - {'common'}
        ensure(not overrides or any(board in group.lower().split('_') for group in overrides),
               'Signed override groups do not include this hardware board.')
        bundles[target] = files
    return manifest_bytes, bundles


def read_cached(root, state, target):
    _, (entries, objects), key = locate(root, state)
    _, bundles = validate_entry(state, entries[key], objects)
    return bundles[target]


def export_pack(root, state, output):
    _, content, key = locate(root, state)
    validate_entry(state, content[0][key], content[1])
    return store.export_entry(content, key, output)


def import_pack(root, state, path):
    entries, objects = store.read(path)
    key = store.entry_key(identity(state))
    ensure(key in entries, 'The bundle pack has no entry for this exact model/board/iOS/build.')
    manifest_bytes, bundles = validate_entry(state, entries[key], objects)
    return save_payload(root, state, manifest_bytes, entries[key]['source_url'], bundles,
                        origin=entries[key].get('origin', 'firmware'))


def validate_manifest(manifest, state):
    expected = identity(state)
    ensure(isinstance(manifest, dict), 'Unsupported BuildManifest: expected a dictionary.')
    ensure(manifest.get('ProductVersion') == expected['version']
           and manifest.get('ProductBuildVersion') == expected['build'], "Firmware does not match the phone's exact iOS version/build.")
    products = manifest.get('SupportedProductTypes')
    ensure(isinstance(products, list) and all(isinstance(v, str) for v in products),
           'Unsupported BuildManifest product list.')
    ensure(expected['product'] in products, "Firmware is for a different iPhone model.")
    identities = manifest.get('BuildIdentities')
    ensure(isinstance(identities, list) and all(isinstance(v, dict) for v in identities),
           'Unsupported BuildManifest identity list.')
    paths = set()
    for candidate in identities:
        info = candidate.get('Info')
        ensure(isinstance(info, dict) and isinstance(info.get('DeviceClass'), str),
               'Unsupported BuildManifest hardware identity.')
        if (info['DeviceClass'].lower() != expected['hardware']
                or candidate.get('Ap,ProductType', expected['product']) != expected['product']):
            continue
        if info.get('Variant') not in {
            'Customer Erase Install (IPSW)',
            'Customer Upgrade Install (IPSW)',
            'Developer Erase Install (IPSW)',
            'Developer Upgrade Install (IPSW)',
        }:
            continue
        component = candidate.get('Manifest')
        for part in ('OS', 'Info'):
            ensure(isinstance(component, dict), 'Unsupported BuildManifest filesystem component.')
            component = component.get(part)
        ensure(isinstance(component, dict) and isinstance(component.get('Path'), str),
               'Unsupported BuildManifest filesystem path.')
        paths.add(component['Path'])
    ensure(len(paths) == 1 and '' not in paths,
           'Cannot identify exactly one supported Customer IPSW filesystem image for this hardware. '
           'A changed firmware format requires an app update.')
    name = next(iter(paths))
    ensure(re.fullmatch(r'[A-Za-z0-9_-]+\.dmg(?:\.aea)?', name), "Unexpected filesystem image path.")
    return name


def save_payload(root, state, manifest_bytes, source, bundles, origin='firmware'):
    """Validate the full pair, deduplicate bytes, and atomically bound the cache."""
    manifest = plistlib.loads(manifest_bytes)
    image = validate_manifest(manifest, state)
    record = {'device': identity(state), 'source_url': source, 'filesystem_image': image, 'origin': origin,
              'manifest': store.digest(manifest_bytes), 'sequence': 0, 'bundles': {}}
    objects = {record['manifest']: manifest_bytes}
    for target in NAMES:
        files = bundles[target]
        info, signatures = validate_files(target, files, build=state.build)
        record['bundles'][target] = {'version': info['CFBundleVersion'], 'signature_groups': sorted(signatures),
                                     'files': {name: store.digest(data) for name, data in sorted(files.items())}}
        objects.update({store.digest(data): data for data in files.values()})
    validate_entry(state, record, objects)
    destination = cache_path(root)
    stats = store.put(destination, record, objects)
    if stats['evicted_entries']:
        print('Cache limit: removed ' + str(stats['evicted_entries']) + ' older prepared entries.', flush=True)
    return destination


def import_carrier_updates(root, state, mvno, wholesale):
    """Accept an original signed update pair for already prepared exact firmware."""
    _, (entries, objects), key = locate(root, state)
    entry = entries[key]
    manifest_bytes, previous = validate_entry(state, entry, objects)
    bundles = {target: read_package(path)[1] for target, path in
               (('mvno', mvno), ('wholesale', wholesale))}
    active_version = str((state.line or {}).get('CFBundleVersion', ''))
    floor = max(version_tuple(active_version), *(version_tuple(plistlib.loads(f['Info.plist'])['CFBundleVersion'])
                                               for f in previous.values()))
    for target, files in bundles.items():
        info, _ = validate_files(target, files, build=state.build)
        ensure(version_tuple(info['CFBundleVersion']) >= floor,
               'Carrier update pair would downgrade an active or cached bundle.')
        if 'MinimumOSVersion' in info:
            ensure(version_tuple(state.version) >= version_tuple(info['MinimumOSVersion']),
                   'Carrier update requires a newer iOS version.')
    return save_payload(root, state, manifest_bytes, entry['source_url'], bundles,
                        origin='imported Apple-signed carrier update pair')


def apple_url(url):
    p = urllib.parse.urlsplit(url)
    ensure(p.scheme == 'https' and p.hostname is not None and not p.username and not p.password
           and (p.hostname == 'apple.com' or p.hostname.endswith('.apple.com')
                or p.hostname == 'cdn-apple.com' or p.hostname.endswith('.cdn-apple.com'))
           and p.port in (None, 443) and p.path.endswith('.ipsw') and not p.fragment,
           "Firmware URL must be an HTTPS Apple-hosted IPSW.")
    return url


def get_bytes(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'LightSpeedSwap/0.5.0 (local firmware importer)'})
    with urllib.request.urlopen(request, timeout=45, context=https_context()) as response:
        data = response.read(limit + 1)
    ensure(len(data) <= limit, "Download exceeded the expected size.")
    return data


def firmware_url(state):
    ident = identity(state)
    # Public model/build only. Never includes UDID, SIM identifiers or GID1.
    result = json.loads(get_bytes('https://api.ipsw.me/v4/device/' + ident['product'] + '?type=ipsw', 16 * 1024 * 1024))
    urls = {v['url'] for v in result.get('firmwares', [])
            if v.get('buildid') == state.build and v.get('version') == state.version}
    ensure(len(urls) == 1, "The firmware index has no unique exact-build image. Supply --firmware-url with its official Apple IPSW URL.")
    return apple_url(next(iter(urls)))


def ensure_ipsw(root):
    if platform.system() in {'Windows', 'Linux'}:
        from carrier_native import prepare_tool
        return prepare_tool(root, get_bytes, 'ipsw')
    ensure(platform.system() == 'Darwin', "Automatic firmware import requires macOS, Windows, or Linux.")
    arch = platform.machine()
    ensure(arch in IPSW_DIGESTS, "Unsupported Mac architecture.")
    folder = data_root(root) / '.carrier-tools' / (IPSW_VERSION + '-' + arch)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = folder / 'ipsw'
    ensure(not binary.is_symlink(), 'Refusing a symlink at the firmware reader path.')
    if binary.exists():
        ensure(binary.stat().st_size < 200 * 1024 * 1024
               and store.digest(binary.read_bytes()) == IPSW_BINARY_DIGESTS[arch],
               'Cached firmware reader checksum mismatch.')
        return binary
    print('Downloading the pinned firmware reader (about 30 MB)...', flush=True)
    url = 'https://github.com/blacktop/ipsw/releases/download/v' + IPSW_VERSION + '/ipsw_' + IPSW_VERSION + '_macOS_' + arch + '.tar.gz'
    data = get_bytes(url, 64 * 1024 * 1024)
    ensure(store.digest(data) == IPSW_DIGESTS[arch], 'Firmware reader archive checksum mismatch.')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
        members = [v for v in tar.getmembers() if Path(v.name).name == 'ipsw']
        ensure(len(members) == 1 and members[0].isfile() and members[0].size < 200 * 1024 * 1024,
               'Unexpected firmware reader archive.')
        with tar.extractfile(members[0]) as source:
            executable = source.read()
        ensure(store.digest(executable) == IPSW_BINARY_DIGESTS[arch], 'Firmware reader executable checksum mismatch.')
        store.atomic_write(binary, executable)
        binary.chmod(0o700)
    # The archive existed only in memory; retain just the verified executable.
    return binary


def ensure_firmware_tools(root):
    tool = ensure_ipsw(root)
    if platform.system() != 'Darwin':
        from carrier_native import prepare_tool
        prepare_tool(root, get_bytes, '7zip')
    return tool


def command(args, timeout=2700, capture=False):
    try:
        result = subprocess.run([str(v) for v in args], check=True, timeout=timeout,
                                stdout=subprocess.PIPE if capture else None)
        return result.stdout if capture else None
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Firmware preparation timed out. The phone was not changed.') from exc


def find_one(work, filename):
    matches = list(Path(work).rglob(filename))
    ensure(len(matches) == 1, 'Firmware extraction did not produce exactly one ' + filename)
    return matches[0]


def extract_macos(image, work, *, build=None):
    mount = Path(work) / 'mount'
    mount.mkdir()
    # No installer is run: this is only a read-only mount of a downloaded image.
    marker = Path(work) / 'mount-state-uncertain.txt'
    marker.write_text('Preserve the backing image until a successful detach.\n')
    command(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', '-mountpoint', mount, image],
            timeout=120, capture=True)
    try:
        base = mount / 'System/Library/Carrier Bundles/iPhone'
        bundles = {target: read_package(base / (name + '.bundle'))[1] for target, name in NAMES.items()}
        for target, files in bundles.items():
            validate_files(target, files, build=build)
        return bundles
    finally:
        # Do not force a detach or delete a possibly still mounted image.
        command(['/usr/bin/hdiutil', 'detach', mount], timeout=60, capture=True)
        marker.unlink()


def extract_files(image, work, root, *, build=None):
    if platform.system() == 'Darwin':
        return extract_macos(image, work, build=build)
    from carrier_native import extract, prepare_tool
    bundles = extract(image, work, prepare_tool(root, get_bytes, '7zip'), NAMES)
    for target, files in bundles.items():
        validate_files(target, files, build=build)
    return bundles


def setup_device(root, state, url=None, bundle_pack=None):
    identity(state)
    if bundle_pack is not None:
        destination = import_pack(root, state, bundle_pack)
        print('Verified and imported the small bundle pack. No firmware download or phone write.', flush=True)
        return destination
    try:
        for target in NAMES:
            read_cached(root, state, target)
    except MissingPayload:
        pass
    else:
        print('Signed payloads for this exact model/build are already cached. Ready to switch.', flush=True)
        return
    url = apple_url(url) if url else firmware_url(state)
    tool = ensure_firmware_tools(root)
    parent = data_root(root) / '.carrier-cache'
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    work = Path(tempfile.mkdtemp(prefix='.firmware-', dir=parent))
    print('Preparing original files for ' + state.product + ' / iOS ' + state.version + ' / ' + state.build, flush=True)
    print('One-time firmware download: several GB. No backup, restore or phone write. Later switches use the small cached bundles.', flush=True)
    try:
        from carrier_remote import RemoteFirmware
        with RemoteFirmware(url) as archive:
            manifest_bytes = archive.manifest()
            image_name = validate_manifest(plistlib.loads(manifest_bytes), state)
            image = work / image_name
            archive.download_image(image_name, image, resume_dir=parent)
        if image.suffix == '.aea':
            command([tool, 'fw', 'aea', image, '--output', image.parent])
            image = image.with_suffix('')
            ensure(image.is_file(), 'Decrypted firmware image was not produced.')
            image.with_suffix(image.suffix + '.aea').unlink()
        bundles = extract_files(image, work, root, build=state.build)
        destination = save_payload(root, state, manifest_bytes, url, bundles)
        print('Verified both original Apple-signed bundles and cached them in one compressed store. Ready to switch.', flush=True)
        return destination
    finally:
        # A failed detach can leave the mount live; preserve its backing image.
        if not os.path.ismount(work / 'mount') and not (work / 'mount-state-uncertain.txt').exists():
            shutil.rmtree(work)
        else:
            print('Firmware mount cleanup is unconfirmed. Working files retained at ' + str(work), flush=True)
