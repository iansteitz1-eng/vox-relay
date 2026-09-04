#!/usr/bin/env bash
# Vox Relay — release build. RUN ON THE MAC (Xcode CLT + Developer ID cert + notarytool profile):
#   ./build_mac.sh
# Produces dist/release/v<version>/ with: VoxRelay-v<version>.zip · SHA256SUMS · RELEASE.sha256 ·
# RELEASE.sha256.sig · allowed_signers · VERSION.json · NOTICE.md, then prints the manifest.json
# block the download rail expects (same shape as Backstage: the manifest.json next to the published release).
set -euo pipefail

# ───────────────────────── FILL THESE (Ian) ─────────────────────────
DEVELOPER_ID="Developer ID Application: InSync Tech inc. (2X8AH6C2HH)"   # exact identity string: `security find-identity -v -p codesigning`
NOTARY_PROFILE="voxordo-notary"                                    # `xcrun notarytool store-credentials <profile>` name
SSH_SIGN_KEY="$HOME/.ssh/vox_recovery_release_ed25519"                   # the ed25519 key that signed Backstage (release@voxordo.io)
# ────────────────────────────────────────────────────────────────────
SIGN_IDENTITY_EMAIL="release@voxordo.io"
SIGN_NAMESPACE="voxrelay-app"                               # Backstage uses backstage-app; verify: -n voxrelay-app -I release@voxordo.io
PRODUCT="voxrelay"
APP_NAME="Vox Relay"
BUNDLE_ID="io.voxordo.voxrelay"
MIN_MACOS="13.0"

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
VERSION="$(tr -d "[:space:]" < VERSION.txt)"
[ -n "$VERSION" ] || { echo "VERSION.txt is empty"; exit 1; }
for v in "$DEVELOPER_ID" "$NOTARY_PROFILE" "$SSH_SIGN_KEY"; do
  case "$v" in *FILL-ME*) echo "✗ fill DEVELOPER_ID / NOTARY_PROFILE / SSH_SIGN_KEY at the top of build_mac.sh"; exit 1;; esac
done
[ "$(uname)" = "Darwin" ] || { echo "✗ run this on the Mac"; exit 1; }
[ -f "$SSH_SIGN_KEY" ] && [ -f "$SSH_SIGN_KEY.pub" ] || { echo "✗ ssh signing key not found: $SSH_SIGN_KEY(.pub)"; exit 1; }
security find-identity -v -p codesigning | grep -Fq "$DEVELOPER_ID" || { echo "✗ identity not in keychain: $DEVELOPER_ID"; exit 1; }

echo "══ Vox Relay v$VERSION — build ══"
DIST="$ROOT/dist"
REL="$DIST/release/v$VERSION"
ZIP_NAME="VoxRelay-v$VERSION.zip"
rm -rf "$DIST" build "$ROOT"/*.spec
mkdir -p "$REL"

# 1 · venv + deps
python3 -m venv "$DIST/venv"
# shellcheck disable=SC1091
source "$DIST/venv/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet rumps pyinstaller
python3 -m pytest tests -q 2>/dev/null || pip install --quiet pytest && python3 -m pytest tests -q

# 2 · PyInstaller (.app, no console)
pyinstaller --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --paths "$ROOT" \
  --hidden-import voxrelay --hidden-import voxrelay.app --hidden-import voxrelay.core --hidden-import rumps \
  --add-data "$ROOT/VERSION.txt:." \
  --distpath "$DIST" --workpath "$DIST/build" --specpath "$DIST" \
  "$ROOT/voxrelay/__main__.py"
APP="$DIST/$APP_NAME.app"
[ -d "$APP" ] || { echo "✗ PyInstaller produced no app"; exit 1; }

# 3 · Info.plist: no Dock icon (LSUIElement), version, min macOS
PLIST="$APP/Contents/Info.plist"
PB=/usr/libexec/PlistBuddy
$PB -c "Delete :LSUIElement" "$PLIST" 2>/dev/null || true
$PB -c "Add :LSUIElement bool true" "$PLIST"
$PB -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" 2>/dev/null || $PB -c "Add :CFBundleShortVersionString string $VERSION" "$PLIST"
$PB -c "Set :CFBundleVersion $VERSION" "$PLIST" 2>/dev/null || $PB -c "Add :CFBundleVersion string $VERSION" "$PLIST"
$PB -c "Set :LSMinimumSystemVersion $MIN_MACOS" "$PLIST" 2>/dev/null || $PB -c "Add :LSMinimumSystemVersion string $MIN_MACOS" "$PLIST"
$PB -c "Set :CFBundleIdentifier $BUNDLE_ID" "$PLIST" 2>/dev/null || true
$PB -c "Add :NSHumanReadableCopyright string Copyright © 2026 InsyncTech. Vox Ordo." "$PLIST" 2>/dev/null || true
# VERSION file inside the bundle so __version__ resolves at runtime
cp "$ROOT/VERSION.txt" "$APP/Contents/Resources/VERSION.txt" 2>/dev/null || true
cp "$ROOT/VERSION.txt" "$APP/Contents/Frameworks/VERSION.txt" 2>/dev/null || true

# 4 · codesign (hardened runtime; Python needs these two entitlements)
ENT="$DIST/entitlements.plist"
cat > "$ENT" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict></plist>
PLIST
echo "── codesign inner binaries"
while IFS= read -r -d '' f; do
  if file "$f" | grep -q "Mach-O"; then
    codesign --force --options runtime --timestamp --entitlements "$ENT" --sign "$DEVELOPER_ID" "$f"
  fi
done < <(find "$APP/Contents" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -u+x \) -print0)
# the build box 2026-09-03: a framework must be sealed as a BUNDLE after its inner Mach-O is re-signed,
# or `codesign --verify --deep --strict` / notarization see a stale seal on Python.framework.
for fw in "$APP"/Contents/Frameworks/*.framework; do
  if [ -d "$fw" ]; then
    echo "── codesign framework bundle: $(basename "$fw")"
    codesign --force --options runtime --timestamp --entitlements "$ENT" --sign "$DEVELOPER_ID" "$fw"
  fi
done
echo "── codesign app"
codesign --force --options runtime --timestamp --entitlements "$ENT" --sign "$DEVELOPER_ID" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

# 5 · notarize + staple
echo "── notarize"
NOTARY_ZIP="$DIST/notarize-$ZIP_NAME"
ditto -c -k --keepParent "$APP" "$NOTARY_ZIP"
xcrun notarytool submit "$NOTARY_ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl -a -vv -t exec "$APP" 2>&1 | tail -2

# 6 · final zip (stapled) + checksums + ssh signature
echo "── package"
ditto -c -k --keepParent "$APP" "$REL/$ZIP_NAME"
cd "$REL"
SHA="$(shasum -a 256 "$ZIP_NAME" | awk '{print $1}')"
BYTES="$(stat -f %z "$ZIP_NAME")"
printf '%s  %s\n' "$SHA" "$ZIP_NAME" > SHA256SUMS
cp SHA256SUMS RELEASE.sha256
ssh-keygen -Y sign -n "$SIGN_NAMESPACE" -f "$SSH_SIGN_KEY" RELEASE.sha256   # → RELEASE.sha256.sig
printf '%s %s\n' "$SIGN_IDENTITY_EMAIL" "$(awk '{print $1" "$2}' "$SSH_SIGN_KEY.pub")" > allowed_signers
ssh-keygen -Y verify -f allowed_signers -I "$SIGN_IDENTITY_EMAIL" -n "$SIGN_NAMESPACE" -s RELEASE.sha256.sig < RELEASE.sha256
shasum -a 256 -c SHA256SUMS

# 7 · VERSION.json + NOTICE.md (mirror Backstage; every claim = a test that ran in step 1 or Ian's POC witness)
GEN="$(date -u +'%Y-%m-%d %H:%MZ')"
TESTS="$(cd "$ROOT" && python3 -m pytest tests -q --collect-only 2>/dev/null | grep '::' | sed 's/.*:://' | sort)"
export ROOT MIN_MACOS TESTS   # the build box 2026-09-03: the python block below reads these via os.environ
python3 - "$PRODUCT" "$VERSION" "$GEN" <<'PY' > VERSION.json
import json, sys, subprocess, os
product, version, gen = sys.argv[1:4]
root = os.environ.get("ROOT") or os.getcwd()
tests = [t for t in os.environ.get("TESTS", "").split("\n") if t]
print(json.dumps({
  "product": product, "version": version, "generated": gen,
  "scorecard_rows_passed": [
    "2026-09-03 M1 POC on Ian's Mac: list + tail worked, real messages came out (vox_relay_poc.py)",
  ] + [f"{gen} unit: {t}" for t in tests],
  "scorecard_rows_open": [
    "clean-Mac install witness (Ian): app opens with no right-click, menu shows, Full Disk Access prompt path works",
    "cloud push end-to-end against POST https://voxordo.ai/v1/relay/messages (server endpoint, not yet live)",
  ],
  "codesign": "developer-id+notarized+stapled",
  "min_macos": os.environ.get("MIN_MACOS", "13.0"),
}, indent=2))
PY
cat > NOTICE.md <<MD
# Vox Relay v$VERSION — what this build is proven to do (generated $GEN)

Vox Relay is sold as the current version it is at: the first, smallest useful build.
Every claim below comes from a test that ran before this build was signed, or from Ian's
own run of the proof of concept on his Mac. Nothing on this page is hand-written marketing.

## Proven
- **Read-only.** The Messages database is opened \`mode=ro&immutable=1\` with \`query_only\`; every
  write statement is refused and no \`-wal\` / \`-journal\` file is created _(unit: test_readonly_open_refuses_writes)_.
- **Only the threads you tick are relayed.** Untick and it stops; nothing is replayed from before
  you ticked _(unit: test_poll_once_ignores_unticked_chats, test_poll_once_appends_exactly_n_and_advances_cursor)_.
- **One JSON line per new message** in \`~/Library/Application Support/VoxRelay/relay.jsonl\`:
  \`{ts, chat_id, handle, is_from_me, text, attachments[], rowid}\` — attachment file *names* only,
  never the files _(unit: test_record_shape_matches_poc, test_push_posts_each_record_when_on)_.
- **Rich-text messages decode** (macOS 13+ stores them in attributedBody) _(unit: test_decode_attributed_body_short_and_long)_.
- **Cloud push is OFF by default and a no-op while off**; when on, each record is POSTed with your
  token; a failed push is logged and the local file stays the record _(unit: test_push_noop_when_off, test_push_failure_is_logged_not_raised)_.
- **M1 witness:** the proof of concept listed chats and tailed a real thread on Ian's Mac (2026-09-03).

## The build you are downloading
- Signed with Developer ID and notarized by Apple — it opens like any Mac app, no right-click
  needed. Integrity also rides the published SHA-256 and the ssh-ed25519 release signature
  (\`RELEASE.sha256.sig\`, namespace \`$SIGN_NAMESPACE\`, signer \`$SIGN_IDENTITY_EMAIL\`).

## What it will NOT do (honestly)
- It does not send anything anywhere unless you turn **Cloud push** on and paste your token.
- It never sends attachment files (photos, videos, documents) — only their file names.
- It does not send messages, mark anything read, or change Messages in any way — read-only.
- It needs **Full Disk Access** (System Settings → Privacy & Security → Full Disk Access); without it
  the Threads list is empty and the app tells you so.
- New messages are noticed on a 5-second check; Messages itself decides when a message reaches
  its database on disk.
- Windows/Linux/iPhone: no. macOS $MIN_MACOS+ only.

## Escalation
Something relayed that should not have (or did not that should)? The log is
\`~/Library/Logs/VoxRelay.log\` — send it with a note to hello@voxordo.ai.

## Verify your download
\`\`\`
shasum -a 256 -c SHA256SUMS
ssh-keygen -Y verify -f allowed_signers -I $SIGN_IDENTITY_EMAIL -n $SIGN_NAMESPACE \\
  -s RELEASE.sha256.sig < RELEASE.sha256
\`\`\`
MD

# 8 · manifest block for the download rail (micro_downloads/voxrelay/manifest.json)
echo
echo "══ DONE → $REL"
ls -la "$REL"
echo
echo "══ manifest.json block for the release host (micro_downloads/$PRODUCT/manifest.json) ══"
cat <<JSON
{
 "product": "$PRODUCT",
 "version": "$VERSION",
 "dir": "v$VERSION",
 "filename": "$ZIP_NAME",
 "sha256": "$SHA",
 "bytes": $BYTES,
 "codesign": "developer-id+notarized+stapled",
 "min_macos": "$MIN_MACOS",
 "public_files": ["SHA256SUMS", "RELEASE.sha256", "RELEASE.sha256.sig", "allowed_signers", "NOTICE.md", "VERSION.json"],
 "versions": {
  "$VERSION": {"dir": "v$VERSION", "filename": "$ZIP_NAME", "sha256": "$SHA", "bytes": $BYTES}
 }
}
JSON
echo
echo "Ship the folder $REL to the release host as micro_downloads/$PRODUCT/v$VERSION/ (zip + the 6 public files) and drop the block above into manifest.json."
