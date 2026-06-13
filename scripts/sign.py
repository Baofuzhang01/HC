#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.reserve import CredentialRejectedError, reserve  # noqa: E402


INDEX_URL = "https://office.chaoxing.com/data/apps/{family}/index"
API_FAMILIES = ("seat", "seatengine")
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def load_config_user(config_path: Path, user_index: int) -> dict[str, Any]:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取配置文件 {config_path}: {exc}") from exc

    users = config.get("reserve")
    if not isinstance(users, list) or not users:
        raise RuntimeError(f"配置文件 {config_path} 中没有可用的 reserve 用户")
    if user_index < 0 or user_index >= len(users):
        raise RuntimeError(
            f"用户序号 {user_index} 超出范围，配置中共有 {len(users)} 位用户"
        )
    if not isinstance(users[user_index], dict):
        raise RuntimeError(f"配置文件中的第 {user_index} 位用户格式无效")
    return users[user_index]


def extract_cur_reserves(payload: Any, seat_id: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("接口响应 success 不为 true")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("接口响应缺少 data 对象")

    cur_reserves = data.get("curReserves")
    if not isinstance(cur_reserves, list):
        raise RuntimeError("接口响应缺少 curReserves 数组")

    return [
        {
            "today": item.get("today"),
            "seatNum": item.get("seatNum"),
            "startTime": format_beijing_time(item.get("startTime")),
            "roomId": item.get("roomId"),
            "seatId": item.get("seatId") or seat_id,
        }
        for item in cur_reserves
        if isinstance(item, dict)
    ]


def format_beijing_time(timestamp_ms: Any) -> str | None:
    if timestamp_ms in (None, ""):
        return None
    try:
        timestamp_seconds = int(timestamp_ms) / 1000
        value = datetime.datetime.fromtimestamp(timestamp_seconds, BEIJING_TZ)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return value.strftime("%H:%M:%S")


def fetch_cur_reserves(
    client: reserve,
    fid_enc: str,
    seat_id: str,
    api_mode: str,
) -> list[dict[str, Any]]:
    families = API_FAMILIES if api_mode == "auto" else (api_mode,)
    errors: list[str] = []

    client.requests.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Host": "office.chaoxing.com",
            "Referer": "https://office.chaoxing.com/",
        }
    )

    for family in families:
        try:
            response = client.requests.get(
                INDEX_URL.format(family=family),
                params={"fidEnc": fid_enc, "seatId": seat_id},
                timeout=client.request_timeout,
                verify=False,
            )
            response.raise_for_status()
            return extract_cur_reserves(response.json(), seat_id)
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            errors.append(f"{family}: {exc}")

    raise RuntimeError("预约信息接口请求失败：" + "; ".join(errors))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="登录超星并提取当前座位预约信息",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config.json",
        help="用户配置文件，默认使用项目根目录下的 config.json",
    )
    parser.add_argument(
        "--user-index",
        type=int,
        default=0,
        help="使用 reserve 数组中的第几个用户，默认 0",
    )
    parser.add_argument("--username", default=os.getenv("CX_USERNAME", ""))
    parser.add_argument("--password", default=os.getenv("CX_PASSWORD", ""))
    parser.add_argument("--fid-enc", default=os.getenv("CX_FID_ENC", ""))
    parser.add_argument("--seat-id", default=os.getenv("CX_SEAT_ID", ""))
    parser.add_argument(
        "--api",
        choices=("auto", *API_FAMILIES),
        default="auto",
        help="请求的接口类型，auto 会依次尝试 seat 和 seatengine",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        user = load_config_user(args.config, args.user_index)
        username = str(args.username or user.get("username") or "").strip()
        password = str(args.password or user.get("password") or "")
        fid_enc = str(args.fid_enc or user.get("fidEnc") or "").strip()
        seat_id = str(
            args.seat_id or user.get("seatPageId") or user.get("roomid") or ""
        ).strip()

        missing = [
            name
            for name, value in (
                ("username", username),
                ("password", password),
                ("fidEnc", fid_enc),
                ("seatId", seat_id),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("缺少必要参数：" + ", ".join(missing))

        client = reserve()
        if not client.bootstrap_login(username, password):
            raise RuntimeError("登录失败")

        result = fetch_cur_reserves(client, fid_enc, seat_id, args.api)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CredentialRejectedError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
