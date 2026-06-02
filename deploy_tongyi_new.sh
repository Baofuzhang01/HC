#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/seat-qianduan.env.local}"
WORKDIR="$ROOT_DIR/workers/tongyi"

read_env_value() {
  local key="$1"
  local value

  value="$(
    python3 - "$ENV_FILE" "$key" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]

if not path.exists():
    raise SystemExit(0)

prefix = key + "="
for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or not line.startswith(prefix):
        continue
    value = line[len(prefix):].strip()
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in ("'", '"')
    ):
        value = value[1:-1]
    print(value)
    break
PY
  )"
  printf "%s" "$value"
}

NEW_CF_ACCOUNT_ID="${NEW_CF_ACCOUNT_ID:-$(read_env_value NEW_CF_ACCOUNT_ID)}"
NEW_CF_KV_NAMESPACE_ID="${NEW_CF_KV_NAMESPACE_ID:-$(read_env_value NEW_CF_KV_NAMESPACE_ID)}"
NEW_CF_API_TOKEN="${NEW_CF_API_TOKEN:-$(read_env_value NEW_CF_API_TOKEN)}"
TARGET_TONGYI_WORKER_NAME="$(
  printf "%s" "${TARGET_TONGYI_WORKER_NAME:-$(read_env_value TARGET_TONGYI_WORKER_NAME)}"
)"
TARGET_TONGYI_WORKER_NAME="${TARGET_TONGYI_WORKER_NAME:-tongyi-new}"

if [[ -z "$NEW_CF_ACCOUNT_ID" ]]; then
  echo "Missing NEW_CF_ACCOUNT_ID. Set it or add it to $ENV_FILE." >&2
  exit 1
fi

if [[ -z "$NEW_CF_KV_NAMESPACE_ID" ]]; then
  echo "Missing NEW_CF_KV_NAMESPACE_ID. Set it or add it to $ENV_FILE." >&2
  exit 1
fi

if [[ -z "$NEW_CF_API_TOKEN" ]]; then
  echo "Missing NEW_CF_API_TOKEN. Set it or add it to $ENV_FILE." >&2
  exit 1
fi

if [[ ! -d "$WORKDIR" ]]; then
  echo "Missing Worker directory: $WORKDIR" >&2
  exit 1
fi

echo "Deploying $TARGET_TONGYI_WORKER_NAME with NEW_CF_ACCOUNT_ID=$NEW_CF_ACCOUNT_ID"
echo "Using KV namespace $NEW_CF_KV_NAMESPACE_ID"

TARGET_CF_ACCOUNT_ID="$NEW_CF_ACCOUNT_ID" \
TARGET_CF_KV_NAMESPACE_ID="$NEW_CF_KV_NAMESPACE_ID" \
TARGET_CF_API_TOKEN="$NEW_CF_API_TOKEN" \
TARGET_TONGYI_WORKER_NAME="$TARGET_TONGYI_WORKER_NAME" \
bash "$ROOT_DIR/deploy_tongyi_target.sh" "$@"
