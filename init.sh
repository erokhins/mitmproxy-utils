#!/usr/bin/env bash
set -euo pipefail

BINARIES=(mitmproxy mitmdump mitmweb)

if command -v mitmweb &>/dev/null; then
  echo "Detected system-wide mitmproxy installation. Creating symlinks..."
  for bin in "${BINARIES[@]}"; do
    path=$(command -v "$bin")
    ln -sf "$path" "./$bin"
    echo "  $bin -> $path"
  done
  echo "Done. Run ./start.sh to launch."
  # Copy reverse proxy config from sample (skip if already exists)
  if [ ! -f reverse_proxy.conf ]; then
    cp reverse_proxy.conf.sample reverse_proxy.conf
    echo "Copied reverse_proxy.conf.sample -> reverse_proxy.conf"
  fi
  exit 0
fi

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

# Copy reverse proxy config from sample (skip if already exists)
if [ ! -f reverse_proxy.conf ]; then
  cp reverse_proxy.conf.sample reverse_proxy.conf
  echo "Copied reverse_proxy.conf.sample -> reverse_proxy.conf"
fi

echo "Done. Run ./start.sh to launch."
