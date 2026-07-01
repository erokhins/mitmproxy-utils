#!/usr/bin/env bash
set -euo pipefail

VERSION="12.2.2"
PLATFORM="linux"
ARCH="x86_64"
ARCHIVE="mitmproxy-${VERSION}-${PLATFORM}-${ARCH}.tar.gz"
URL="https://downloads.mitmproxy.org/${VERSION}/${ARCHIVE}"

echo "Downloading mitmproxy ${VERSION}..."
curl -fL "$URL" -o "$ARCHIVE"

echo "Extracting..."
tar -xzf "$ARCHIVE" mitmproxy mitmdump mitmweb
chmod +x mitmproxy mitmdump mitmweb
rm "$ARCHIVE"

echo "Done. Run ./start.sh to launch."
