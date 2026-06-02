#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPORT_PATH = PROJECT_ROOT / "tongyi-kv-export.json"
DEFAULT_ENV_EXPORT_PATH = PROJECT_ROOT / "tongyi-env-migration.env"
DEFAULT_EXCLUDED_EXPORT_PREFIXES = ["meta:heartbeat:"]

SERVER_ENV_KEYS = [
    "CF_ACCOUNT_ID",
    "CF_KV_NAMESPACE_ID",
    "CF_API_TOKEN",
    "FLASK_SECRET_KEY",
    "SERVER_PROJECT_ROOT",
    "SEAT_STORE_DB_PATH",
    "SERVER_DISPATCH_HOST",
    "SERVER_DISPATCH_PORT",
    "SERVER_DISPATCH_API_KEY",
    "ENABLE_RESERVE_RESULT_CENTER",
    "RESERVE_RESULT_REPORT_TOKEN",
    "RESERVE_RESULT_CENTER_URL",
    "RESERVE_RESULT_SERVER_ID",
    "RESERVE_RESULT_REPORT_TIMEOUT",
    "ENABLE_RESERVE_RESULT_REPORT_TIMER",
    "RESERVE_RESULT_LOCAL_WRITE",
    "SERVER_WORKER2_ENABLED",
    "SERVER_WORKER2_TRIGGER_API",
    "SERVER_WORKER2_API_KEY",
    "SERVER_WORKER2_UI_KEY",
    "SERVER_WORKER2_HEARTBEAT_SOURCE_ACCOUNT_ID",
    "SERVER_WORKER2_HEARTBEAT_SOURCE_NAMESPACE_ID",
    "SERVER_WORKER2_HEARTBEAT_SOURCE_API_TOKEN",
    "SERVER_WORKER2_FEISHU_WEBHOOK",
    "SERVER_WORKER2_FEISHU_KEYWORD",
    "SERVER_WORKER2_RECORDS_DIR",
    "SERVER_WORKER2_SCHEDULE_FILE",
    "SERVER_WORKER2_ALLOW_TIMER_EDIT",
    "SERVER_WORKER2_TIMER_UNIT_PATH",
    "SERVER_WORKER2_TIMER_NAME",
    "SERVER_WORKER2_SERVICE_NAME",
    "SERVER_WORKER2_UI_HOST",
    "SERVER_WORKER2_UI_PORT",
    "CHAOJIYING_USERNAME",
    "CHAOJIYING_PASSWORD",
    "CHAOJIYING_SOFT_ID",
    "CHAOJIYING_CODETYPE",
    "TULINGCLOUD_USERNAME",
    "TULINGCLOUD_PASSWORD",
    "TULINGCLOUD_MODEL_ID",
    "TULINGCLOUD_SPIN_MODEL_ID",
]

WORKER_SECRET_NAMES = [
    "API_KEY",
    "GH_TOKEN",
    "GH_TOKEN_A",
    "GH_TOKEN_B",
    "GH_TOKEN_C",
    "GH_TOKEN_D",
    "GH_TOKEN_E",
    "SERVER_DISPATCH_API_KEY",
]


def load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path).expanduser()
    if not env_path.exists():
        raise SystemExit(f"env file not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
                os.environ[key] = value


def parse_env_file(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    env_path = Path(path).expanduser()
    if not env_path.exists():
        raise SystemExit(f"env file not found: {env_path}")
    result: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def env_or_arg(args, attr: str, env_names: str | list[str], label: str) -> str:
    names = [env_names] if isinstance(env_names, str) else env_names
    value = str(getattr(args, attr, "") or "").strip()
    if not value:
        for env_name in names:
            value = str(os.getenv(env_name, "")).strip()
            if value:
                break
    if not value:
        raise SystemExit(
            f"missing {label}: pass --{attr.replace('_', '-')} or set one of {', '.join(names)}"
        )
    return value


class CloudflareKV:
    def __init__(self, account_id: str, namespace_id: str, api_token: str):
        self.account_id = account_id
        self.namespace_id = namespace_id
        self.base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/storage/kv/namespaces/{namespace_id}"
        )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        response = self.session.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)
        if response.status_code >= 400:
            detail = response.text[:1000]
            raise RuntimeError(f"Cloudflare API {method} {url} failed: {response.status_code} {detail}")
        return response

    def list_keys(self, prefix: str = "") -> list[dict[str, Any]]:
        keys: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params = {"limit": 1000}
            if prefix:
                params["prefix"] = prefix
            if cursor:
                params["cursor"] = cursor
            response = self._request("GET", f"{self.base_url}/keys", params=params)
            payload = response.json()
            if not payload.get("success", False):
                raise RuntimeError(f"Cloudflare list keys failed: {payload}")
            keys.extend(payload.get("result") or [])
            info = payload.get("result_info") or {}
            cursor = str(info.get("cursor") or "")
            if not cursor:
                return keys

    def get_value_bytes(self, key: str) -> bytes | None:
        response = self.session.get(f"{self.base_url}/values/{key}", timeout=30)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RuntimeError(
                f"Cloudflare get value failed for {key!r}: {response.status_code} {response.text[:1000]}"
            )
        return response.content

    def put_value_bytes(self, key: str, value: bytes, metadata: Any = None) -> None:
        files = None
        headers = None
        if metadata is not None:
            files = {
                "value": (None, value),
                "metadata": (None, json.dumps(metadata, ensure_ascii=False)),
            }
        else:
            headers = {"Content-Type": "text/plain; charset=utf-8"}
        response = self.session.put(
            f"{self.base_url}/values/{key}",
            data=value if files is None else None,
            files=files,
            headers=headers,
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Cloudflare put value failed for {key!r}: {response.status_code} {response.text[:1000]}"
            )


def build_source_client(args) -> CloudflareKV:
    return CloudflareKV(
        env_or_arg(args, "source_account_id", ["SOURCE_CF_ACCOUNT_ID", "CF_ACCOUNT_ID"], "source account id"),
        env_or_arg(
            args,
            "source_namespace_id",
            ["SOURCE_CF_KV_NAMESPACE_ID", "CF_KV_NAMESPACE_ID"],
            "source KV namespace id",
        ),
        env_or_arg(args, "source_api_token", ["SOURCE_CF_API_TOKEN", "CF_API_TOKEN"], "source API token"),
    )


def build_target_client(args) -> CloudflareKV:
    return CloudflareKV(
        env_or_arg(args, "target_account_id", "TARGET_CF_ACCOUNT_ID", "target account id"),
        env_or_arg(args, "target_namespace_id", "TARGET_CF_KV_NAMESPACE_ID", "target KV namespace id"),
        env_or_arg(args, "target_api_token", "TARGET_CF_API_TOKEN", "target API token"),
    )


def selected_keys(
    keys: list[dict[str, Any]],
    include_prefixes: list[str],
    exclude_prefixes: list[str],
) -> list[dict[str, Any]]:
    result = []
    for item in keys:
        name = str(item.get("name") or "")
        if include_prefixes and not any(name.startswith(prefix) for prefix in include_prefixes):
            continue
        if exclude_prefixes and any(name.startswith(prefix) for prefix in exclude_prefixes):
            continue
        result.append(item)
    return result


def export_kv(args) -> None:
    source = build_source_client(args)
    output_path = Path(args.output).expanduser()
    include_prefixes = args.prefix or []
    exclude_prefixes = [] if args.include_heartbeat else DEFAULT_EXCLUDED_EXPORT_PREFIXES + (args.exclude_prefix or [])

    keys = selected_keys(source.list_keys(), include_prefixes, exclude_prefixes)
    records: list[dict[str, Any]] = []
    started = time.time()
    for index, item in enumerate(keys, start=1):
        key = str(item.get("name") or "")
        if not key:
            continue
        value = source.get_value_bytes(key)
        if value is None:
            continue
        record = {
            "key": key,
            "value_base64": base64.b64encode(value).decode("ascii"),
        }
        if "metadata" in item and item.get("metadata") is not None:
            record["metadata"] = item.get("metadata")
        if "expiration" in item and item.get("expiration") is not None:
            record["expiration"] = item.get("expiration")
        records.append(record)
        if index % 50 == 0:
            print(f"exported {index}/{len(keys)} keys...", file=sys.stderr)

    payload = {
        "format": "tongyi-cloudflare-kv-export-v1",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "account_id": source.account_id,
            "namespace_id": source.namespace_id,
        },
        "key_count": len(records),
        "excluded_prefixes": exclude_prefixes,
        "records": records,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "action": "export",
                "output": str(output_path),
                "keys": len(records),
                "excluded_prefixes": exclude_prefixes,
                "seconds": round(time.time() - started, 3),
            },
            ensure_ascii=False,
        )
    )


def import_kv(args) -> None:
    if not args.yes:
        raise SystemExit("refusing to import without --yes")
    target = build_target_client(args)
    input_path = Path(args.input).expanduser()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    records = payload.get("records") or []
    if not isinstance(records, list):
        raise SystemExit("invalid export file: records must be a list")

    started = time.time()
    imported = 0
    for index, record in enumerate(records, start=1):
        key = str(record.get("key") or "")
        encoded = str(record.get("value_base64") or "")
        if not key or not encoded:
            continue
        value = base64.b64decode(encoded)
        target.put_value_bytes(key, value, metadata=record.get("metadata"))
        imported += 1
        if index % 50 == 0:
            print(f"imported {index}/{len(records)} keys...", file=sys.stderr)

    print(
        json.dumps(
            {
                "ok": True,
                "action": "import",
                "input": str(input_path),
                "target_account_id": target.account_id,
                "target_namespace_id": target.namespace_id,
                "keys": imported,
                "seconds": round(time.time() - started, 3),
            },
            ensure_ascii=False,
        )
    )


def verify_kv(args) -> None:
    target = build_target_client(args)
    input_path = Path(args.input).expanduser()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    records = payload.get("records") or []
    checked = 0
    missing: list[str] = []
    different: list[str] = []

    for record in records:
        key = str(record.get("key") or "")
        encoded = str(record.get("value_base64") or "")
        if not key or not encoded:
            continue
        expected = base64.b64decode(encoded)
        actual = target.get_value_bytes(key)
        checked += 1
        if actual is None:
            missing.append(key)
        elif actual != expected:
            different.append(key)

    result = {
        "ok": not missing and not different,
        "action": "verify",
        "checked": checked,
        "missing": missing,
        "different": different,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if missing or different:
        raise SystemExit(1)


def quote_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(ch.isspace() for ch in value) or any(ch in value for ch in "#'\"\\$`"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        return f'"{escaped}"'
    return value


def env_export(args) -> None:
    source_values = parse_env_file(args.input_env)
    output_path = Path(args.output).expanduser()
    target_overrides = {
        "CF_ACCOUNT_ID": args.target_account_id,
        "CF_KV_NAMESPACE_ID": args.target_namespace_id,
        "CF_API_TOKEN": args.target_api_token,
    }

    lines: list[str] = [
        "# Generated by scripts/migrate_tongyi_kv.py env-export",
        "# This file is for the new server/account after Tongyi KV migration.",
        "# Cloudflare Worker secret values cannot be pulled back from Cloudflare;",
        "# fill the TODO items below and run wrangler secret put for each one.",
        "",
        "# Server / local sync environment",
    ]
    for key in SERVER_ENV_KEYS:
        value = str(target_overrides.get(key) or source_values.get(key) or os.getenv(key, "")).strip()
        if not value:
            lines.append(f"{key}=")
        else:
            lines.append(f"{key}={quote_env_value(value)}")

    lines.extend(
        [
            "",
            "# Tongyi Worker secrets to set in the target Cloudflare account.",
            "# These are placeholders because Cloudflare does not expose secret plaintext.",
        ]
    )
    for key in WORKER_SECRET_NAMES:
        local_value = str(source_values.get(key) or os.getenv(key, "")).strip()
        if local_value:
            lines.append(f"# {key} is present locally; set it with: wrangler secret put {key}")
        else:
            lines.append(f"# TODO: wrangler secret put {key}")

    lines.extend(
        [
            "",
            "# Optional migration helper variables",
            f"SOURCE_CF_ACCOUNT_ID={quote_env_value(str(source_values.get('CF_ACCOUNT_ID') or ''))}",
            f"SOURCE_CF_KV_NAMESPACE_ID={quote_env_value(str(source_values.get('CF_KV_NAMESPACE_ID') or ''))}",
            "SOURCE_CF_API_TOKEN=",
            f"TARGET_CF_ACCOUNT_ID={quote_env_value(args.target_account_id or '')}",
            f"TARGET_CF_KV_NAMESPACE_ID={quote_env_value(args.target_namespace_id or '')}",
            "TARGET_CF_API_TOKEN=",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "action": "env-export",
                "input": str(Path(args.input_env).expanduser()) if args.input_env else "",
                "output": str(output_path),
                "server_keys": len(SERVER_ENV_KEYS),
                "worker_secret_placeholders": len(WORKER_SECRET_NAMES),
            },
            ensure_ascii=False,
        )
    )


def add_common_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-account-id", default="")
    parser.add_argument("--source-namespace-id", default="")
    parser.add_argument("--source-api-token", default="")


def add_common_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target-account-id", default="")
    parser.add_argument("--target-namespace-id", default="")
    parser.add_argument("--target-api-token", default="")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export/import Tongyi Cloudflare KV data between accounts")
    parser.add_argument("--env-file", default="", help="Optional env file to load before reading SOURCE_/TARGET_ variables")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export source KV to a local JSON file")
    add_common_source_args(export_parser)
    export_parser.add_argument("--output", default=str(DEFAULT_EXPORT_PATH))
    export_parser.add_argument(
        "--prefix",
        action="append",
        default=[],
        help="Only export keys with this prefix. Repeatable. Omit to export all keys.",
    )
    export_parser.add_argument(
        "--exclude-prefix",
        action="append",
        default=[],
        help="Exclude keys with this prefix. Repeatable. meta:heartbeat: is excluded by default.",
    )
    export_parser.add_argument(
        "--include-heartbeat",
        action="store_true",
        help="Include meta:heartbeat:* keys. By default heartbeat keys are skipped.",
    )
    export_parser.set_defaults(func=export_kv)

    import_parser = subparsers.add_parser("import", help="Import a JSON export into target KV")
    add_common_target_args(import_parser)
    import_parser.add_argument("--input", default=str(DEFAULT_EXPORT_PATH))
    import_parser.add_argument("--yes", action="store_true", help="Required confirmation for writes")
    import_parser.set_defaults(func=import_kv)

    verify_parser = subparsers.add_parser("verify", help="Compare target KV values against a JSON export")
    add_common_target_args(verify_parser)
    verify_parser.add_argument("--input", default=str(DEFAULT_EXPORT_PATH))
    verify_parser.set_defaults(func=verify_kv)

    env_parser = subparsers.add_parser("env-export", help="Generate a target env template from a local env file")
    env_parser.add_argument("--input-env", default=str(PROJECT_ROOT / "seat-qianduan.env.local"))
    env_parser.add_argument("--output", default=str(DEFAULT_ENV_EXPORT_PATH))
    env_parser.add_argument("--target-account-id", default="")
    env_parser.add_argument("--target-namespace-id", default="")
    env_parser.add_argument("--target-api-token", default="")
    env_parser.set_defaults(func=env_export)

    args = parser.parse_args()
    load_env_file(args.env_file)
    args.func(args)


if __name__ == "__main__":
    main()
