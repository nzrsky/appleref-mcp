#!/usr/bin/env bash
# Pack the essential parts of an Apple_API_Reference.docset into a
# tar.xz suitable for a GitHub release asset.
#
# Usage:
#   tools/pack-docset.sh [<docset-path>] [<out-dir>]
#
# Defaults:
#   docset-path = $APPLEREF_DOCSET, else the Dash default location
#   out-dir     = ./dist
#
# Output:
#   <out-dir>/appleref-docset-<tag>.tar.xz
#   <out-dir>/appleref-docset-<tag>.tar.xz.sha256
#   <out-dir>/appleref-docset-<tag>.notes.md
#
# The tag is derived from the docset's Info.plist XcodeDocsVersion +
# BuildVersion. Pure metadata, no network.

set -euo pipefail

DOCSET="${1:-${APPLEREF_DOCSET:-$HOME/Library/Application Support/Dash/DocSets/Apple_API_Reference/Apple_API_Reference.docset}}"
OUT_DIR="${2:-$(pwd)/dist}"

if [[ ! -d "$DOCSET" ]]; then
    echo "error: docset not found at: $DOCSET" >&2
    exit 1
fi

INDEX="$DOCSET/Contents/Resources/optimizedIndex.dsidx"
CACHE_DB="$DOCSET/Contents/Resources/Documents/cache.db"
FS_DIR="$DOCSET/Contents/Resources/Documents/fs"
INFO_PLIST="$DOCSET/Contents/Info.plist"
VERSION_PLIST="$DOCSET/Contents/Resources/Documents/version.plist"

for f in "$INDEX" "$CACHE_DB" "$INFO_PLIST"; do
    [[ -f "$f" ]] || { echo "error: missing $f" >&2; exit 1; }
done
[[ -d "$FS_DIR" ]] || { echo "error: missing $FS_DIR" >&2; exit 1; }

read_plist() {
    /usr/libexec/PlistBuddy -c "Print :$1" "$2" 2>/dev/null || echo ""
}

XCODE_VER="$(read_plist XcodeDocsVersion "$INFO_PLIST")"
BUILD_VER="$(read_plist BuildVersion "$VERSION_PLIST")"
CF_VER="$(read_plist CFBundleVersion "$VERSION_PLIST")"

if [[ -n "$BUILD_VER" && -n "$CF_VER" ]]; then
    TAG="${CF_VER}-${BUILD_VER}"
elif [[ -n "$XCODE_VER" ]]; then
    TAG="$(echo "$XCODE_VER" | tr -c '[:alnum:]' '-' | sed 's/--*/-/g; s/-$//')"
else
    TAG="$(date +%Y.%m.%d)"
fi

mkdir -p "$OUT_DIR"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/appleref-pack.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

ROOT="$STAGE/Apple_API_Reference.docset"
mkdir -p "$ROOT/Contents/Resources/Documents"

cp "$INFO_PLIST"    "$ROOT/Contents/Info.plist"
cp "$INDEX"         "$ROOT/Contents/Resources/optimizedIndex.dsidx"
cp "$CACHE_DB"      "$ROOT/Contents/Resources/Documents/cache.db"
[[ -f "$VERSION_PLIST" ]] && cp "$VERSION_PLIST" "$ROOT/Contents/Resources/Documents/version.plist"
cp -R "$FS_DIR"     "$ROOT/Contents/Resources/Documents/fs"

ARCHIVE="$OUT_DIR/appleref-docset-${TAG}.tar.xz"
SHASUM="${ARCHIVE}.sha256"
NOTES="$OUT_DIR/appleref-docset-${TAG}.notes.md"

THREADS="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)"
echo ">> packing $TAG using $THREADS threads → $ARCHIVE"

# Pack with xz multi-threaded. -6 is a good speed/ratio tradeoff for a 1.4 GB
# tree dominated by a SQLite FTS4 index. -9 buys a few % at much higher cost.
( cd "$STAGE" && tar -cf - Apple_API_Reference.docset ) \
    | xz -T "$THREADS" -6 -c > "$ARCHIVE"

# sha256 in `<hash>  <basename>` form so `shasum -c` works portably
( cd "$OUT_DIR" && shasum -a 256 "$(basename "$ARCHIVE")" > "$SHASUM" )

RAW_SIZE="$(du -sh "$STAGE/Apple_API_Reference.docset" | cut -f1)"
ARCHIVE_SIZE="$(du -sh "$ARCHIVE" | cut -f1)"
HASH="$(cut -d' ' -f1 "$SHASUM")"

cat > "$NOTES" <<EOF
Apple API Reference docset, packed from Dash.

| field | value |
| --- | --- |
| Xcode docs version | \`${XCODE_VER:-n/a}\` |
| Docset build | \`${BUILD_VER:-n/a}\` (CFBundleVersion \`${CF_VER:-n/a}\`) |
| Unpacked size | ${RAW_SIZE} |
| Archive size | ${ARCHIVE_SIZE} |
| sha256 | \`${HASH}\` |

Contains only the files the MCP server reads:
- \`Contents/Info.plist\`
- \`Contents/Resources/optimizedIndex.dsidx\`
- \`Contents/Resources/Documents/cache.db\`
- \`Contents/Resources/Documents/version.plist\`
- \`Contents/Resources/Documents/fs/\`

The server downloads this asset automatically on first run if no local
docset is found. Set \`APPLEREF_AUTO_DOWNLOAD=0\` to disable.
EOF

echo ">> done"
echo "   archive: $ARCHIVE  ($ARCHIVE_SIZE)"
echo "   sha256:  $HASH"
echo "   tag:     docset-$TAG"
echo "   notes:   $NOTES"
echo
echo "publish:"
echo "   gh release create docset-$TAG \\"
echo "     --title 'Apple docset $TAG' \\"
echo "     --notes-file '$NOTES' \\"
echo "     '$ARCHIVE' '$SHASUM'"
