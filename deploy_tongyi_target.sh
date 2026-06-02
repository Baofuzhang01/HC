#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKDIR="$ROOT_DIR/workers/tongyi"

TARGET_CF_ACCOUNT_ID="${TARGET_CF_ACCOUNT_ID:-}"
TARGET_CF_KV_NAMESPACE_ID="${TARGET_CF_KV_NAMESPACE_ID:-}"
TARGET_CF_API_TOKEN="${TARGET_CF_API_TOKEN:-${CF_API_TOKEN_WORKER2:-}}"
TARGET_TONGYI_WORKER_NAME="${TARGET_TONGYI_WORKER_NAME:-tongyi-new}"

if [[ -z "$TARGET_CF_ACCOUNT_ID" ]]; then
  echo "Missing TARGET_CF_ACCOUNT_ID." >&2
  exit 1
fi

if [[ -z "$TARGET_CF_KV_NAMESPACE_ID" ]]; then
  echo "Missing TARGET_CF_KV_NAMESPACE_ID." >&2
  exit 1
fi

if [[ -z "$TARGET_CF_API_TOKEN" ]]; then
  echo "Missing TARGET_CF_API_TOKEN. Export the new-account token first." >&2
  exit 1
fi

TMP_CONFIG="$(mktemp "$WORKDIR/tongyi-target-wrangler.XXXXXX.toml")"
trap 'rm -f "$TMP_CONFIG"' EXIT

python3 - "$WORKDIR/wrangler.toml" "$TMP_CONFIG" "$TARGET_CF_ACCOUNT_ID" "$TARGET_CF_KV_NAMESPACE_ID" "$TARGET_TONGYI_WORKER_NAME" <<'PY'
from pathlib import Path
import re
import sys

src, dst, account_id, kv_namespace_id, worker_name = sys.argv[1:6]
text = Path(src).read_text(encoding="utf-8")
text = re.sub(r'^name\s*=\s*".*"$', f'name = "{worker_name}"', text, flags=re.MULTILINE)
text = re.sub(r'^account_id\s*=\s*".*"$', f'account_id = "{account_id}"', text, flags=re.MULTILINE)
text = re.sub(r'(^binding\s*=\s*"SEAT_KV"\s*\n^id\s*=\s*)".*"', rf'\1"{kv_namespace_id}"', text, flags=re.MULTILINE)
Path(dst).write_text(text, encoding="utf-8")
PY

cd "$WORKDIR"
CLOUDFLARE_API_TOKEN="$TARGET_CF_API_TOKEN" npx wrangler deploy --config "$TMP_CONFIG" "$@"
