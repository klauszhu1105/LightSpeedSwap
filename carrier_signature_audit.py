#!/usr/bin/env python3
"""Offline Apple carrier signature verifier derived from 24B119 firmware.

Recognizes the T-Mobile common files and board-specific override groups.
Unknown layouts/signature keys fail closed. Verified on the original V63/V64
and N104 firmware files. Signature validity does not prove device selection.
"""
import argparse
import hashlib
import json
import plistlib
import re
import stat
import struct
import sys
import zipfile
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils


# Public, uncompressed P-384 point. Extracted at VA 0x2c2a8ee1b, length 0x61,
# from CarrierBundleUtilities.dylib in 24B119's actual SystemOS dyld cache.
APPLE_PRODUCTION_P384 = bytes.fromhex(
    "04de7893741a21ef21e5fe21fc5838034fed5ba8666e83bc29a29501da497727f32"
    "ed7796dd138d3b8550e4eef9cf53490059e615cd260ed0d53f37c781ebd92efdd"
    "7c6779afa27cf617269d603a2eb544a61c45dfcd337643ebb4f28b548eaa97"
)
# Public P-384 point at extracted dylib offset 0xfdba in 24C5037i SystemOS.
APPLE_24C5037I_P384 = bytes.fromhex(
    "0491fd89e6c211bbce45f3eabc9555433ea1731e77341fc6f8c794ffc8b6248c"
    "78dbd0b00b7ec0c8932d1ad0a9ba6bc12f2c0098a2cf64f61f6aa77d3273276e"
    "cc70f83e9676a4a8237d3336ecafc64c888968a8ce3c1635c321b3e57954ad93"
    "43"
)
MAX_BYTES = 10 * 1024 * 1024


def read_package(path):
    """Read local input only; ZIP members are never extracted to disk."""
    path = Path(path)
    if path.is_dir():
        entries = list(path.rglob("*"))
        if any(p.is_symlink() for p in entries):
            raise ValueError("Symlink semantics are not modeled by this audit")
        files = [p for p in entries if p.is_file()]
        if len(files) > 100 or sum(p.stat().st_size for p in files) > MAX_BYTES:
            raise ValueError("Input exceeds this tool's carrier-bundle scope")
        return path.name, {p.relative_to(path).as_posix(): p.read_bytes() for p in files}
    with zipfile.ZipFile(path) as z:
        members = [i for i in z.infolist() if not i.is_dir()]
        if len(members) > 100 or sum(i.file_size for i in members) > MAX_BYTES:
            raise ValueError("Input exceeds this tool's carrier-bundle scope")
        paths = [PurePosixPath(i.filename) for i in members]
        if any(p.is_absolute() or ".." in p.parts for p in paths):
            raise ValueError("Unsupported ZIP member path")
        if any(stat.S_ISLNK(i.external_attr >> 16) for i in members):
            raise ValueError("Symlink semantics are not modeled by this audit")
        roots = {p.parts[:2] for p in paths if len(p.parts) >= 3
                 and p.parts[0] == "Payload" and p.parts[1].endswith(".bundle")}
        if len(roots) != 1:
            raise ValueError("Expected one Payload/*.bundle in the IPCC")
        root = next(iter(roots))
        if any(p.parts[:2] != root for p in paths):
            raise ValueError("Extra files outside the bundle are not modeled")
        result = {}
        for i, p in zip(members, paths):
            name = PurePosixPath(*p.parts[2:]).as_posix()
            if name in result:
                raise ValueError("Duplicate ZIP member")
            result[name] = z.read(i)
        return root[-1], result


def classify(name):
    """Recognize signed T-Mobile common files and arbitrary board overrides."""
    p = PurePosixPath(name)
    if p.parts[0] == "signatures" or p.name == "version.plist":
        return None
    if len(p.parts) != 1:
        raise ValueError("Nested/special bundle layouts are not modeled: " + name)
    if p.name.startswith("overrides_"):
        m = re.fullmatch(r"overrides_([A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)\.(plist|der\.pri|pri)", p.name)
        if not m:
            raise ValueError("Unmodeled override file: " + name)
        return m.group(1)
    if p.name in {"CarrierCA.crt", "Info.plist", "carrier.plist", "carrier.pri", "device+carrier.plist"}:
        return "common"
    raise ValueError("Unmodeled file: " + name)


def canonical_file(name, data):
    # Native readFile() converts *.plist using CFPropertyListCreateData(XML).
    # Python's sorted XML serialization was validated against all six original
    # signed groups here. This is not a universal CF serializer equivalence claim.
    if name.endswith(".plist"):
        return plistlib.dumps(plistlib.loads(data), fmt=plistlib.FMT_XML, sort_keys=True)
    return data


def check_signatures(files, *, build=None):
    if (not isinstance(files, dict) or not files or len(files) > 100
            or not all(isinstance(n, str) and n and isinstance(v, bytes) for n, v in files.items())
            or sum(map(len, files.values())) > MAX_BYTES):
        raise ValueError('Unsupported carrier file collection')
    grouped = {}
    for name in sorted(files):
        group = classify(name)
        if group is not None:
            grouped.setdefault(group, []).append(name)
    if "common" not in grouped or not {"Info.plist", "carrier.plist"} <= set(files):
        raise ValueError("Missing common carrier files")
    expected_signatures = {"signatures/" + ("common" if g == "common" else "overrides_" + g) + ".plist"
                           for g in grouped}
    actual_signatures = {n for n in files if n.startswith("signatures/")}
    if expected_signatures != actual_signatures:
        raise ValueError("Signature files do not correspond exactly to the payload groups")
    public_point = APPLE_24C5037I_P384 if build == "24C5037i" else APPLE_PRODUCTION_P384
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP384R1(), public_point)
    results = {}
    digests = {}
    for group in ["common"] + sorted(set(grouped) - {"common"}):
        h = hashlib.sha384()
        if group != "common":
            h.update(digests["common"])
        for name in grouped[group]:
            h.update(canonical_file(name, files[name]))
        for name in grouped[group]:
            encoded = PurePosixPath(name).name.encode("utf-8")
            h.update(struct.pack(">I", len(encoded)))
            h.update(encoded)
        digests[group] = h.digest()
        signature_file = "signatures/" + ("common" if group == "common" else "overrides_" + group) + ".plist"
        document = plistlib.loads(files[signature_file])
        if not isinstance(document, dict) or not isinstance(document.get('CBSignature3'), bytes):
            raise ValueError('Unsupported Apple carrier signature format: CBSignature3 is required. '
                             'A changed signing format requires an app update.')
        signature = document['CBSignature3']
        try:
            key.verify(signature, digests[group], ec.ECDSA(utils.Prehashed(hashes.SHA384())))
            valid = True
        except InvalidSignature:
            valid = False
        results[group] = {"signature_valid": valid, "sha384": h.hexdigest(),
                          "covered_files": grouped[group]}
    return results


def valid_all(files):
    return all(r["signature_valid"] for r in check_signatures(files).values())


def mutation_experiments(files):
    """In-memory controls; never writes modified bundles or sends them to iOS."""
    baseline = valid_all(files)
    if not baseline:
        raise ValueError("Original signatures must verify before running controls")
    outcomes = []
    scenarios = []
    original_id = plistlib.loads(files["Info.plist"])["CFBundleIdentifier"]
    wholesale = "Wholesale" in original_id

    changed = dict(files)
    carrier = plistlib.loads(changed["carrier.plist"])
    carrier["SupportedSIMs"] = ["310260_GID1-DD" if wholesale else "310260_GID1-1A53"]
    changed["carrier.plist"] = plistlib.dumps(carrier)
    scenarios.append(("replace SupportedSIMs with opposite target", changed, False))

    changed = dict(files)
    info = plistlib.loads(changed["Info.plist"])
    info["CFBundleIdentifier"] = "com.apple.TMobile_MVNO_US" if wholesale else "com.apple.TMobile_Wholesale_US"
    changed["Info.plist"] = plistlib.dumps(info)
    scenarios.append(("replace CFBundleIdentifier", changed, False))

    changed = dict(files)
    changed["device+carrier.plist"] = changed.pop("carrier.plist")
    scenarios.append(("rename carrier.plist to overlay-style filename", changed, False))

    # Root directory names are not digest inputs; this does not modify signed
    # identity or SIM selectors and is not evidence of installation/selection.
    scenarios.append(("rename outer bundle directory only (digest invariance)", dict(files), True))

    changed = dict(files)
    changed["version.plist"] = plistlib.dumps({"SupportedSIMs": ["310260_GID1-DD"]})
    scenarios.append(("add version.plist (excluded from this signature; installer reads carrier.plist)", changed, True))

    for name, modified, expected in scenarios:
        try:
            result = valid_all(modified)
        except (ValueError, KeyError):
            result = False
        outcomes.append({"experiment": name, "all_signatures_valid": result,
                         "expected": expected, "control_passed": result == expected})
    return outcomes


def audit(path, experiments=False, match=None):
    directory, files = read_package(path)
    info = plistlib.loads(files["Info.plist"])
    carrier = plistlib.loads(files["carrier.plist"])
    result = {"mode": "offline; no phone access", "directory": directory,
              "bundle_identifier": info.get("CFBundleIdentifier"),
              "bundle_version": info.get("CFBundleVersion"),
              "supported_sims": carrier.get("SupportedSIMs"),
              "signature_groups": check_signatures(files),
              "proves_device_installation_or_selection": False}
    if match is not None:
        result["exact_selector_present"] = match in carrier.get("SupportedSIMs", [])
    if experiments:
        result["in_memory_experiments"] = mutation_experiments(files)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("package", type=Path, help="Local .bundle directory or one-bundle .ipcc")
    p.add_argument("--experiments", action="store_true", help="Run in-memory mutation controls")
    p.add_argument("--match", help="Check exact signed selector, e.g. 310260_GID1-DD")
    args = p.parse_args()
    try:
        result = audit(args.package, args.experiments, args.match)
        print(json.dumps(result, indent=2))
        return 0 if all(r["signature_valid"] for r in result["signature_groups"].values()) else 2
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, plistlib.InvalidFileException) as e:
        print("Audit could not model input: " + str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
