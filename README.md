<p align="center">
  <img src="assets/lightspeedswap-logo.png" alt="LightSpeedSwap" width="620">
</p>

# LightSpeedSwap


**Important note: You can switch the same eSIM back to MVNO without replacing it. What I should’ve made clearer is that revert leaves LightSpeedSwap’s SIM-specific matching rule on the phone. Even after you switch back, iOS may keep using the older settings installed by LightSpeedSwap when Apple ships a newer carrier bundle. I’ve tested switching to Wholesale and back on my iPhone. I haven’t verified which bundle wins on a real phone after an iOS or carrier settings update. If you use LightSpeedSwap, be prepared to refresh the bundle manually. After an update, run status and check both the bundle name and its version. Seeing “MVNO” or “Wholesale” alone won’t tell you whether you’re up to date.**

A compact Python CLI for switching **TMobile_MVNO_US ↔ TMobile_Wholesale_US**
on an eligible iPhone running **iOS 27 or later**. It detects the active bundle,
uses original Apple-signed files for the exact firmware, installs once, restarts
once, and verifies the selected SIM afterward.

For **US Mobile Light Speed**, the Wholesale bundle enables the carrier settings
used for **5G Standalone (SA)** and the **5G UC indicator**. **SA and UC have been
tested and confirmed working by the project owner on an iPhone 18 Pro, US model.**
This is a known-working device report; other models, firmware, SIM provisioning,
and coverage can produce different results. A UC icon alone does not prove SA
or a change in network priority. See [the test evidence](VALIDATION.md#physical-sa-and-uc-confirmation).

**No backup, restore, OS downgrade, or Find My change.** `--skip-backup` remains
accepted for compatibility; every command always skips backups.

CarrierSIM reports its AirTraffic method blocked beginning with iOS 27.2 beta 3
(24B5099f). LightSpeedSwap uses a separate paired IPCC installation route with
the original signed carrier files. It does not modify the signed settings.

## Step-by-step usage guide (this fork)

LightSpeedSwap is an upstream project by
[ThomasPServo](https://github.com/ThomasPServo/LightSpeedSwap). This fork adds the
following walkthrough; the upstream instructions, limitations, release links,
and dependency credits are retained below. Read the important note above before
installing carrier settings.

### 1. Initialize macOS and check the phone

Use Terminal on an Intel or Apple Silicon Mac with Python 3.9+ available as
`python3`. If it is missing, install Python 3.12 from
[python.org](https://www.python.org/downloads/macos/) first. Download the upstream
CLI and checksum into a dedicated folder, verify them, then initialize the private
runtime (network access is required):

```sh
mkdir -p ~/Downloads/LightSpeedSwap
cd ~/Downloads/LightSpeedSwap
curl -fL -o LightSpeedSwap.pyz https://github.com/ThomasPServo/LightSpeedSwap/releases/latest/download/LightSpeedSwap.pyz
curl -fL -o SHA256SUMS.txt https://github.com/ThomasPServo/LightSpeedSwap/releases/latest/download/SHA256SUMS.txt
shasum -a 256 -c SHA256SUMS.txt
python3 LightSpeedSwap.pyz setup
python3 LightSpeedSwap.pyz doctor
```

Stop if checksum verification fails. `setup` prepares the runtime and dependencies;
it does not prepare firmware or write to a phone. No manual `sudo`, Homebrew,
Rust, or OpenSSL development installation is required. For a source checkout,
run `python3 launcher.py setup` in the repository and replace
`python3 LightSpeedSwap.pyz` below with `python3 launcher.py`. The source launcher
and zipapp use different default state directories; keep using the same entry
point so your cache and restoration receipts stay together.

Connect the iPhone by USB, unlock it, accept **Trust This Computer**, and run:

```sh
python3 LightSpeedSwap.pyz status
```

Record the four public firmware fields: `product`, `hardware`, `version`, and
`build`. Also check `carrier_rows` for each line's `slot`, `bundle`, and carrier
`version`; the carrier version is different from the iOS version. A
`needs_setup_or_repair` payload status means the resources need preparation or
repair before switching.

For dual SIM, copy the exact `slot` value for the intended US Mobile Light Speed
line. **`kOne` below is only an example, not a universal slot number.** Replace it
in every command for your line, including preparation, switching, and reverting:

```sh
python3 LightSpeedSwap.pyz status --slot kOne
```

Do not select a regular T-Mobile, AT&T, or Verizon line. If the CLI reports an
ambiguous second T-Mobile line or incomplete selector metadata, temporarily
disable the other line on the phone and check status again. Do not guess SIM IDs
or GID1 to bypass a refusal.

### 2. Prepare an exact-build carrier pack with GitHub Actions

A `.carrierpack` contains both original signed MVNO and Wholesale bundles for an
exact **product + hardware board + iOS version + build**. A matching iOS version
alone is insufficient. Preparation is host-only; it does not switch the phone.

In **your fork**, open [Actions → Prepare carrier pack](https://github.com/klauszhu1105/LightSpeedSwap/actions/workflows/carrier-pack.yml).
Enable workflows if GitHub prompts you, choose **Run workflow**, select the branch
containing the workflow, and enter the following fields. You need write access
to run it; no phone connection or Apple ID is needed on the runner.

| Workflow input | Example only | How to choose |
| --- | --- | --- |
| `host` | `macos-26` | Use an offered runner option; this is the default. |
| `product` | `iPhone19,3` | Copy `product` from local `status`. |
| `hardware` | `V64AP` | Copy `hardware` from local `status`. |
| `version` | `27.3` | Copy the exact iOS `version` from `status`. |
| `build` | `24C5049d` | Copy the exact `build`, including its suffix. |
| `firmware_url` | Official Apple URL below | Use the public HTTPS IPSW URL for that exact firmware, or leave blank for exact-build lookup. |

For the **24C5049d / iPhone19,3 / V64AP / iOS 27.3 example only**, the supplied
Apple IPSW URL is:

```text
https://updates.cdn-apple.com/2026FallSeed/dd121378-fe01-45c8-8fe8-7c16b8b577b8/iPhone19,2,iPhone19,3,iPhone19,7_27.3_24C5049d_Restore.ipsw
```

**These are not universal parameters or a claim of on-phone validation.** Use
them only when all four fields match your connected phone. The shared IPSW
filename lists several products; it does not make their hardware boards
interchangeable. The builder checks the actual manifest, board, and signatures
and stops on a mismatch. A beta/RC may be absent from the lookup index, which is
when supplying its official URL is useful. Do not substitute a nearby build or
an unofficial mirror. Workflow URLs must be public and have no query parameters,
credentials, or access tokens.

Run the workflow and wait for the **prepare** job to succeed. Open the completed
run and download its **Artifacts → `LightSpeedSwap-carrier-pack-<run-id>`** ZIP.
Artifacts expire after **14 days**. This downloads the small verified pack and
receipts, not the IPSW. An ordinary README commit does not trigger firmware
preparation; use **Run workflow** for this procedure.

### 3. Verify the artifact and import it locally

Unzip the artifact into a local folder. It contains one `.carrierpack`,
`provenance.json`, and `SHA256SUMS.txt`. In Terminal, change to that extracted
folder and run:

```sh
shasum -a 256 -c SHA256SUMS.txt
```

Check that `provenance.json` records the expected four identity fields, source
URL, and workflow commit. A checksum detects changed bytes; it does not establish
that an untrusted pack's firmware labels are truthful. Use a pack from your own
verified workflow run or another trusted source.

Return to the folder containing `LightSpeedSwap.pyz`, keep the matching phone
connected, and import the **unpacked file**, not the artifact ZIP. Replace the
path and slot below with your actual values:

```sh
python3 LightSpeedSwap.pyz setup-device --slot kOne --bundle-pack "$HOME/Downloads/prepared-pack/LightSpeedSwap-iPhone19-3-v64ap-27.3-24C5049d.carrierpack"
python3 LightSpeedSwap.pyz status --slot kOne
```

The builder normalizes the board to lowercase (`v64ap`) in metadata and the pack
filename; GitHub's artifact name is different. Import checks the connected
phone's exact identity and the signed files again, caches both directions, and performs no phone write or
firmware download. You do not need to copy the IPSW onto the Mac.

Alternatively, prepare locally with `python3 LightSpeedSwap.pyz setup-device
--slot kOne`, adding `--firmware-url '<official Apple HTTPS IPSW URL>'` when
needed. Do not combine `--firmware-url` and `--bundle-pack`. Local extraction can
download several GB and needs about **25 GiB or more** of temporary space;
Actions avoids that local firmware workload. Already available exact-build
payloads may be reused by local `setup-device`.

### 4. Switch, verify, and return

```sh
python3 LightSpeedSwap.pyz switch --slot kOne --to wholesale
python3 LightSpeedSwap.pyz status --slot kOne
```

Allow the restart, unlock the phone after it boots, and verify the selected line's
bundle **and version**. Check calls, data, and IMS service on the phone. An artifact
build or an accepted installation alone does not prove activation, SA, UC, or
network priority. Service may be interrupted during the change. On a timeout or
unconfirmed result, reconnect and run `status` before another switch.

To explicitly select MVNO, or instead return to the original bundle recorded for
this same SIM, choose the appropriate command:

```sh
# Explicitly select MVNO:
python3 LightSpeedSwap.pyz switch --slot kOne --to mvno
# Alternatively, restore this SIM's recorded original bundle:
python3 LightSpeedSwap.pyz revert --slot kOne
```

`revert` requires the original host restoration receipt; it refuses if the
baseline is unknown. **It restores the bundle choice, not a clean uninstall**:
the installed SIM-specific selector can remain and affect future carrier updates.
Neither returning to MVNO nor reverting requires replacing the eSIM. Keep the
state directory and its receipts. `--no-reboot` leaves a manual restart pending;
`--reinstall` is an explicit switch option, not a way to bypass downgrade checks.

### 5. After an iOS update, refresh resources; clean up deliberately

After upgrading iOS, run `status` again and use the **new** four identity fields
to prepare/import a matching pack (or rerun local `setup-device`). Then run
`switch --slot kOne --to wholesale` or `--to mvno` as desired and verify with
`status`. Do not reuse the example build or an old pack for a new build. An
already active target can still need newer carrier files. The CLI refuses older
carrier versions; if a carrier-only update is newer than the firmware's files,
see [iOS and carrier updates](#ios-and-carrier-updates) for signed update pairs.

```sh
python3 LightSpeedSwap.pyz cache info
# Discard only an unfinished large firmware download:
python3 LightSpeedSwap.pyz cache clear-download
# Clear only the downloaded/prepared bundle cache:
python3 LightSpeedSwap.pyz cache clear
```

These two clear commands preserve restoration receipts and do not undo a switch
on the phone. `cache clear` keeps included payloads; `clear-download` keeps
verified bundles. An interrupted local download normally resumes when you rerun
`setup-device`, so clear it only when you intend to discard progress. Successful
preparation removes temporary expanded images unless macOS unmount cleanup is
unconfirmed; follow any reported retained-image path and confirm it is unmounted
before removing it. Manually exported packs and downloaded artifact ZIPs are
separate files you can delete when no longer needed. Do not delete the entire
state directory to clear a firmware download.

### Safety and sharing limits

Only use an eligible, paired phone and the intended line. Signature, manifest,
identity, and downgrade refusals are safety boundaries; do not edit signed files
or disable HTTPS validation. There is no backup, restore, iOS downgrade, Find My
change, automatic OTA refresh, or guarantee that future iOS builds will work.

Workflow inputs need only public firmware metadata. Never put IMSI, ICCID, UDID,
GID1, Apple ID credentials, passwords, tokens, or private device logs in workflow
inputs, commits, issues, or screenshots. A device-derived selector `.ipcc` from
`prepare` contains full SIM identities and must remain private. A public firmware
`.carrierpack` is a different format and contains no SIM selector. Do not commit
private selectors, restoration state, credentials, generated artifacts, IPSWs,
decrypted images, or large firmware caches. This walkthrough requires no such
files in git.

## Download and run

Download [LightSpeedSwap.pyz from the latest release](https://github.com/ThomasPServo/LightSpeedSwap/releases/latest/download/LightSpeedSwap.pyz)
and its [SHA256SUMS.txt](https://github.com/ThomasPServo/LightSpeedSwap/releases/latest/download/SHA256SUMS.txt).
The [release page](https://github.com/ThomasPServo/LightSpeedSwap/releases/latest)
includes the release notes and source archives.
This is a universal **Python zipapp**, not a native standalone executable.
Python 3.9+ starts it; explicit one-time setup prepares a private Python 3.11+
runtime with the tested dependencies. Python and dependencies are not embedded
in the small app download.

**Intel Mac setup is supported in 0.7.1 and later.** Download the new `.pyz` and run
`python3 LightSpeedSwap.pyz setup` again, including after a failed 0.7.0 install.
No Homebrew, Rust, OpenSSL development package, or manual runtime deletion is
needed for this fix. Existing carrier cache and restoration receipts are kept.
Setup uses a SHA-256-pinned Intel build of **cryptography 50.0.2** maintained by
[Espressif](https://github.com/espressif/idf-python-wheels#macos-intel-x86_64-and-cryptography),
with OpenSSL included. Apple Silicon, Windows x64, and Linux retain their PyPI
packages. The Intel wheel is downloaded only when needed; it is not embedded
in the CLI. A missing or changed package stops setup instead of compiling it or
silently installing an older cryptography version.

**0.7.2 fixes macOS certificate errors during `setup-device`.** Replace the
previous download and run `python3 LightSpeedSwap.pyz setup` once, then retry
`setup-device`. Firmware-index, Apple range, and helper downloads now load an
explicit certifi CA bundle alongside Python's configured trust roots. Certificate
and hostname checks stay enabled; no system certificate installation is needed.
The OpenSSL legacy-provider warning is separate from HTTPS certificate trust.

On macOS/Linux:

```sh
python3 LightSpeedSwap.pyz setup
python3 LightSpeedSwap.pyz status
python3 LightSpeedSwap.pyz setup-device
python3 LightSpeedSwap.pyz switch --to wholesale
python3 LightSpeedSwap.pyz switch --to mvno
```

On Windows (PowerShell or Command Prompt):

```powershell
py -3 LightSpeedSwap.pyz setup
py -3 LightSpeedSwap.pyz status
py -3 LightSpeedSwap.pyz setup-device
py -3 LightSpeedSwap.pyz switch --to wholesale
py -3 LightSpeedSwap.pyz switch --to mvno
```

**Firmware preparation runs locally on macOS, Windows, and Linux.** Windows
needs Apple's device drivers/services (installed with Apple Devices); Linux
needs working usbmuxd and permission to access its socket. These USB prerequisites
are separate from firmware extraction. USB behavior is not established by
offline tests or by successfully preparing a carrier pack.

| Host | Firmware preparation |
| --- | --- |
| macOS, Apple Silicon or Intel | Read-only APFS mount using `hdiutil` |
| Windows, x64 | Direct APFS reading with pinned `ipsw` and 7-Zip helpers |
| Windows 11, ARM64 | Same reader; setup selects x64 Python for dependency compatibility |
| Linux, x86-64 or ARM64 | Direct APFS reading with pinned native helpers |

Windows/Linux extraction needs no Mac, WSL, virtual machine, filesystem driver,
FUSE mount, or administrator/root access. On Windows ARM, the private Python
runtime uses Windows' x64 emulation because the pinned cryptography release has
no ARM64 wheel. The setup bootstrap handles this without downgrading dependencies.

Unlock and trust the connected iPhone. Unlock it again after the restart.
`--no-reboot` leaves the restart to you and reports it as pending. If the target
bundle is already reported active, nothing is installed; use `--reinstall` only
when you explicitly want another installation.

From source, use `python3 launcher.py setup` and `python3 launcher.py status`.
The existing `bash Setup.command` and `bash Run.command ...` launchers also work.

## Exact firmware without a growing collection of files

There is no fixed phone-model or firmware-build allowlist. `setup-device` checks
the connected product, hardware board, iOS version, and build, then retrieves
that exact firmware's carrier pair. An absent beta/RC index entry can use:

```sh
python3 LightSpeedSwap.pyz setup-device --firmware-url '<official Apple HTTPS IPSW URL>'
```

The manifest must match all four identity fields. The normal Customer IPSW OS
image is selected; Recovery and unknown install variants are not substituted.
Original carrier signatures and applicable board overrides must verify.
Signed files are never edited to make a package pass verification.

Prepared payloads occupy **one compressed, deduplicated cache**, capped at
**8 MiB and eight exact model/build entries**. An entry contains both directions.
The oldest prepared entries are evicted automatically when necessary. Oversized
new entries or failed writes leave the previous cache intact. Unknown cache
schemas stop with an explicit update requirement.

The included seven-model/build pack is 362,992 bytes: 39 unique objects replace
116 file references. The complete CLI is under 500 KiB.

```sh
python3 LightSpeedSwap.pyz cache info
python3 LightSpeedSwap.pyz cache clear
python3 LightSpeedSwap.pyz export-pack --output MyPhone.carrierpack
python3 LightSpeedSwap.pyz setup-device --bundle-pack MyPhone.carrierpack
```

Exported packs contain original signed files and public firmware metadata,
not SIM identifiers or a backup. Imports need no firmware download. Only import
packs you prepared or trust: carrier signatures authenticate the carrier bytes,
not an arbitrary pack author's firmware labels. Export never overwrites a file.

The `.pyz` stores its private runtime and cache under:

| Host | Default state directory |
| --- | --- |
| macOS | `~/Library/Application Support/LightSpeedSwap` |
| Linux | `$XDG_DATA_HOME/LightSpeedSwap`, or `~/.local/share/LightSpeedSwap` |
| Windows | `%LOCALAPPDATA%\LightSpeedSwap` |

`CARRIER_SELECTOR_HOME` can specify an absolute alternative. The source launcher
defaults to its source directory. Zipapp code is unpacked into a temporary folder
for each run and removed afterward; the runtime and bounded cache are shared
across app updates. A crashed cache writer can leave a lock; close app processes
before removing a stale lock reported by the app.

**Temporary space is different from retained cache size.** An unfamiliar firmware
requires several GB of download; reserve about 25 GiB or more. The app checks the
specific image's disk requirements and credits bytes already saved for resuming.
HTTP ranges stream only the selected IPSW member using bounded reads, not the
entire IPSW or sparse extraction from an encrypted APFS image.

**Slow downloads can continue beyond 45 minutes in 0.7.3.** Each request retains a
45-second socket inactivity timeout and up to three attempts for transient
connection errors or temporary server failures. Invalid ranges, certificate
failures, and non-Apple redirects are never bypassed. There is no total elapsed
download cutoff while the transfer keeps making progress.

If a connection fails or you interrupt `setup-device`, rerun that command to
resume. One unfinished compressed filesystem member and its small public source
receipt are kept under `.carrier-cache`, outside the 8 MiB verified-payload cap.
Both stored and deflated ZIP members resume without fetching the saved prefix
again. The saved bytes are reread/decompressed locally and the complete member
CRC is checked before decryption or extraction; original Apple carrier signatures
are still required before caching usable bundles. A changed URL, archive size,
or member metadata restarts that one pending download. Corrupt saved data is
rejected and discarded. To explicitly discard a pending download:

```sh
python3 LightSpeedSwap.pyz cache clear-download
```

This keeps verified bundles and restoration receipts. `cache clear` still clears
only prepared bundles. After a successful download the large checkpoint is
removed; after successful preparation the expanded images are removed as well.
Windows/Linux never mount the image. On macOS, uncertain unmount cleanup
preserves the backing image and reports its location. An older release that
already deleted a failed download cannot recover that earlier progress.

The optional firmware helpers are downloaded on demand into `.carrier-tools`
under the state directory. They use about 83–109 MiB, depending on the host:
`ipsw` 3.1.741 decrypts AEA, and Windows/Linux additionally use 7-Zip 26.05 to
read APFS. Archive, executable, and DLL SHA-256 hashes are pinned and checked
before use. Windows' 7-Zip installer is unpacked as an archive; it is never run
as an installer or added to PATH. Only the two named carrier directories are
extracted, then their original Apple signatures are checked again.

Runtime/dependencies, helpers, pending downloads, and manually exported packs are outside the
payload cache cap. No helper binary or full firmware image is shipped in this
repo or embedded in the CLI. An unfamiliar image container, unsupported APFS
feature, or changed helper output stops preparation without writing to the phone.

## Prepare bundles with GitHub Actions

Use [Prepare carrier pack](https://github.com/ThomasPServo/LightSpeedSwap/actions/workflows/carrier-pack.yml)
to do the large download and extraction on a GitHub-hosted Mac, Windows, or
Linux runner. Run `status` locally, then choose **Run workflow**, select `host`,
and enter its four public fields:
`product`, `hardware`, `version`, and `build`. Do not enter SIM identifiers.
Leave `firmware_url` blank for exact-build lookup, or provide the public Apple
IPSW URL for a beta/RC that is not indexed. Running the workflow requires write
access to the repository; other users can run it in their own fork.

After the job succeeds, download and unzip its `LightSpeedSwap-carrier-pack-…`
artifact. It contains one small `.carrierpack` with both bundles, `provenance.json`,
and `SHA256SUMS.txt`. With the matching phone connected, import it and switch:

```sh
python3 LightSpeedSwap.pyz setup-device --bundle-pack /path/to/LightSpeedSwap-….carrierpack
python3 LightSpeedSwap.pyz switch --to wholesale
```

The CLI verifies the pack again against the connected phone's exact model,
board, version, and build. The same pack supports `--to mvno`. The workflow
does not contact a phone or create a SIM selector. The existing CLI already
supports this import; no app update is needed.

The job fetches a fresh firmware image even if that identity is included in the
CLI. It checks the manifest, complete downloaded ZIP member CRC, both original
Apple signature sets, and board overrides before publishing. The receipt records
image/manifest/file hashes and the workflow commit. It uploads no IPSW, decrypted
image, or device identifiers, and does not add generated packs to git. Artifacts
expire after 14 days, so keep your downloaded pack if you need it later.

The runner checks free space first. When needed, the Mac runner reclaims unused
Xcode applications and the Linux runner reclaims unused Android/.NET SDKs.
Windows selects the temporary drive with the most free space. This space-preparation
script refuses to run outside disposable GitHub-hosted jobs. Extraction stops
before downloading if insufficient space remains; unusually
large future images can need more space. Unknown formats or failed validation
produce no artifact. Prepared packs establish file compatibility, not successful
on-phone activation. Firmware bundles may also be older than a carrier-only
update; the existing downgrade checks still apply.

For a request tracked in git, edit only the public fields in
[`.github/carrier-pack-request.json`](.github/carrier-pack-request.json) and commit
it to `main`. This also triggers preparation. Ordinary source commits do not
start a large firmware download. The standalone builder can run on a local Mac,
Windows PC, or Linux machine (use x64 Python on Windows):

```sh
python -m pip install -r requirements.txt
python build_carrier_pack.py --request .github/carrier-pack-request.json --output-dir ./prepared-pack
```

The output directory must not already exist. The runner space-cleanup script is
not needed for local use. Each local host still needs the temporary disk space
described above.

## Compatibility and evidence

See [VALIDATION.md](VALIDATION.md) for actual firmware and host test coverage.
The user's physical confirmation covers both directions on
**iPhone19,2 / V63AP / iOS 27.2 / 24B119**, with reboot needed to refresh remaining
settings. The owner also confirms **SA and the UC indicator working on a US
iPhone 18 Pro**. The SA/UC report does not separately identify the CLI revision
or test method; the detailed validation record preserves that scope. Other
phone/build combinations are experimental until physically tested.

Future versions with the supported formats can be prepared without changing a
model/build list. Changed signature keys, unknown signing formats, missing carrier
files, ambiguous OS images, or unusable USB metadata stop clearly. Synthetic
future-version tests establish parser behavior only. They cannot guarantee that
a future iOS release will retain this selector/install mechanism.

The selected line must already report MVNO or Wholesale, an IMSI beginning
`310240` or `310260`, a usable ICCID, and a nonempty GID1. `--gid1` accepts a
confirmed value when unavailable; a supplied value cannot shorten a reported
GID1 and broaden its match. No carrier name or log guess supplies missing IDs.

With multiple lines, select the exact `Slot` value from status using `--slot`.
Identifiers must belong to that line; root metadata is never borrowed from a
different line. If two T-Mobile lines match the same selector, temporarily
disable the other line yourself. The app does not disable SIMs.

Regular **T-Mobile (`TMobile_US`), AT&T, and Verizon are rejected as selected
targets**. Packages contain only the chosen MVNO/Wholesale bundle and a selector
derived from that line's full identity. Other line identities never enter that
selector. Offline isolation checks do not establish physical dual-SIM behavior.

## eSIM hygiene and returning to the original bundle

New selectors use the **full IMSI, full ICCID, and GID1**. Removal or ordinary
replacement of the eSIM makes that selector stop matching, including a replacement
with the same old seven/nine-digit prefixes. This behavior was checked against
24B119 parser/scoring machine-code excerpts; it is not a physical replacement test.
The native comparison remains prefix-based, not a new exact-equality API. Reusing
the same full SIM identities can reactivate an installed selector.

Before an installation, the app records the original bundle name in a small
private host receipt. It stores keyed fingerprints of the device/SIM identity,
not the identifiers themselves or a copy of phone settings. Failed or interrupted
operations keep their original baseline and are marked unconfirmed. To return
the same SIM to its recorded original bundle using currently verified files:

```sh
python3 LightSpeedSwap.pyz revert
```

A different SIM cannot inherit that receipt. `status` reports the baseline and
whether software changed since the last operation. Keep the state directory if
you want `revert` to remember it. Clearing the payload cache preserves receipts.

**Upgrading from the earlier prefix-selector app:** that existing selector does
not disappear merely because this release uses a narrower one. For the original
MVNO baseline in the earlier experiment, run once on the same SIM:

```sh
python3 LightSpeedSwap.pyz migrate-legacy --original mvno
python3 LightSpeedSwap.pyz switch --to wholesale
```

Migration replaces that known broad selector's payload with the specified
original bundle, then restarts and verifies. It refuses ambiguous simultaneous
T-Mobile lines. Do not supply an original bundle you do not know. This command
has not been physically tested, and does not remove other historical selectors.

**This is functional isolation and baseline restoration, not a clean uninstall.**
Installed files and normal carrier aliases can remain. The reviewed paired-USB
service exposes carrier installation, not a validated carrier-removal operation.
A CLI cannot delete files or react while closed/disconnected. Automatic return to
the exact pre-run filesystem state after deleting an eSIM is therefore not claimed.

## iOS and carrier updates

After an iOS update, run `setup-device` again. Payload identity includes the exact
new version/build, so an old firmware cache is never silently reused. Then run
`switch` for the desired target. Even when its bundle identifier is already active,
a newer prepared carrier version is installed and verified by version as well as
SIM identity. A newer active carrier version is never replaced with older files.

If Apple ships a carrier-only update newer than the firmware's bundled files,
prepare the exact firmware first, then import both original signed update IPCCs:

```sh
python3 LightSpeedSwap.pyz update-carriers --mvno-ipcc MVNO.ipcc --wholesale-ipcc Wholesale.ipcc
python3 LightSpeedSwap.pyz switch --to wholesale
```

Both identities, signatures, board overrides, versions, and a declared minimum
OS version are checked. An invalid/incomplete/downgrade pair leaves the cache
untouched. Importing updates is host-only; it does not install them. An exported
pack preserves the carrier-update provenance.

This does not promise automatic Apple OTA behavior: an installed custom selector
can outrank later broad carrier candidates. Reconnect and refresh after updates.
There is no background updater, no invented download URL for unavailable carrier
IPCCs, and no guarantee of compatibility with an unreleased iOS selector mechanism.

One install has a 75-second limit, reboot/reconnect has a 180-second limit, and
same-SIM activation verification has a 30-second limit. A 420-second process
deadline also bounds uncooperative service cleanup. An installer success alone
does not prove activation. Failures do not trigger repeated installs or restarts.
An accepted operation cannot be undone by timing out; reconnect and run status.

Check voice, data, and IMS service on the phone after a switch. Returning to
MVNO replaces this selector's payload; it does not remove unrelated historical
experiments. SIM, carrier, and OS updates can change selection.

## Other commands

```sh
python3 LightSpeedSwap.pyz doctor
python3 LightSpeedSwap.pyz prepare --to wholesale --output Wholesale-selector.ipcc
python3 LightSpeedSwap.pyz diagnose --seconds 45 --output carrier-diagnostic.json
python3 LightSpeedSwap.pyz --version
```

`doctor` checks the local runtime and included signatures without a phone.
`prepare` creates a private package without installing it. `diagnose` reads a bounded,
redacted trace; review it before sharing. No reports are uploaded. Full IMSI,
ICCID, and UDID are never uploaded. Full IMSI/ICCID appear in the private selector
package and its on-phone directory name, and may appear in device logs; do not
share that IPCC. Host restoration receipts contain only keyed fingerprints.
Firmware lookup sends only the public product model to IPSW.me.

Exit codes: `0` success/already active; `2` prerequisite/error; `3` activation or
reboot unconfirmed; `4` manual reboot required; `124` hard deadline; `130` interrupted.

## Development

```sh
python -m pip install -r requirements.txt
python -m unittest -q
python build_cli.py
```

The build produces only `dist/LightSpeedSwap.pyz` and its checksum. Its stored
outer ZIP members make the output reproducible across Python/zlib versions;
the carrier pack inside is already compressed. Tests never access a phone.
CI covers Python 3.11–3.14 on Linux, Windows, Apple Silicon macOS, and Intel
macOS 15. Separate Intel macOS 15 and 26 jobs exercise the actual zipapp setup
with Python 3.14, unavailable compilers/OpenSSL development files, an incomplete
old environment, and repeated setup. They verify that restoration records and
cached files survive, and that the crypto extension has no Homebrew linkage.
These are host setup tests, not physical USB/iPhone tests or a promise that
every obsolete macOS release supports the dependency set.

Release publishing is separate from firmware preparation. The
[`Publish release`](.github/workflows/release.yml) workflow validates the version
and SHA-256 in [`.github/release-request.json`](.github/release-request.json),
rebuilds the CLI byte-for-byte, and publishes only the CLI and its checksum as
release assets. Update the request and `RELEASE_NOTES.md` for a new version.
Publishing waits for all compatibility and Intel setup checks to pass.
Existing releases are never overwritten automatically.

Dependencies: [pymobiledevice3](https://github.com/doronz88/pymobiledevice3),
[cryptography](https://github.com/pyca/cryptography), and optional
[blacktop/ipsw](https://github.com/blacktop/ipsw/tree/v3.1.741) and
[7-Zip](https://github.com/ip7z/7zip/releases/tag/26.05) helpers.
The downloaded 7-Zip helper retains its original `License.txt`; its source and
licensing terms are available in the [upstream repository](https://github.com/ip7z/7zip/blob/26.05/DOC/License.txt).
Apple carrier files remain unmodified and are not relicensed as application code.
