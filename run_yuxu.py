from __future__ import annotations

import argparse
import getpass
import json
import sys

from yuxu import __version__
from yuxu import server, store
from yuxu.contract import BusinessError


def setup() -> None:
    if store.is_initialized():
        raise BusinessError("conflict", "本机用户已初始化；如需调整账号，请先联系维护者。", code="already_initialized")
    print("余序首次本机设置：为两位使用者分别创建独立账本、密码和 MCP 令牌。")
    accounts = []
    for index in (1, 2):
        print(f"\n使用者 {index}")
        username = input("本机用户名：").strip()
        account_name = input("账本显示名：").strip()
        password = getpass.getpass("本机密码（至少 1 个字符）：")
        repeated = getpass.getpass("再次输入密码：")
        if password != repeated:
            raise BusinessError("needs_clarification", "两次输入的密码不一致，请重新运行 setup。", code="password_mismatch")
        accounts.append({"username": username, "account_name": account_name, "password": password})
    admin_username = input("管理员用户名（留空表示两人都只访问本人账本）：").strip() or None
    maintenance_username = input("本机可信维护者用户名（须独立选择，可与管理员相同）：").strip()
    if maintenance_username.casefold() not in {account["username"].strip().casefold() for account in accounts}:
        raise BusinessError("needs_clarification", "维护者必须从两位本机用户中明确选择。", code="invalid_maintenance_user")
    conn = store.connect()
    try:
        created = store.create_local_accounts(conn, accounts, admin_username, maintenance_username)
    finally:
        conn.close()
    print("\n本机初始化完成。以下 MCP 令牌只显示这一次，请分别复制到对应使用者的 MCP 客户端环境变量 YUXU_TOKEN。")
    for account in created:
        print(f"{account['username']} / {account['account_name']}: host_token_id={account['host_token_id']}; scopes={','.join(account['host_scopes'])}; YUXU_TOKEN={account['mcp_token']}")
    print("初始宿主只有汇总读取权限；如需查看明细或写入，请本人登录本机网页后明确授权。")
    print("令牌数据库仅保存哈希；服务仅绑定 127.0.0.1。")


def main() -> int:
    parser = argparse.ArgumentParser(prog="run_yuxu.py", description="余序本机账本服务")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="首次本机初始化两位使用者")
    serve_parser = commands.add_parser("serve", help="启动本机网页与 API 服务")
    serve_parser.add_argument("--background-child", action="store_true", help=argparse.SUPPRESS)
    serve_parser.add_argument("--owner", choices=("local-cli", "web-cli", "web-background", "mcp-sdk"), default="local-cli", help=argparse.SUPPRESS)
    web_parser = commands.add_parser("launch-web", help="启动本机网页服务")
    web_parser.add_argument("--no-open", action="store_true", help="不打开默认浏览器")
    web_parser.add_argument("--background", action="store_true", help="启动后台服务后退出终端启动器")
    maintenance_parser = commands.add_parser("grant-maintenance", help="明确指定一位本机账本用户管理备份与 Web 恢复")
    maintenance_parser.add_argument("username", help="已有本机用户名；重新执行会替换原维护者")
    commands.add_parser("mcp", help="通过 MCP stdio 连接本机服务")
    commands.add_parser("status", help="查看脱敏的本机服务状态")
    commands.add_parser("stop", help="停止本机服务")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup()
        elif args.command == "serve":
            server.serve(owner=args.owner)
        elif args.command == "launch-web":
            server.launch_web(open_browser=not args.no_open, background=args.background)
        elif args.command == "grant-maintenance":
            conn = store.connect()
            try:
                granted = store.grant_maintenance(conn, args.username)
            finally:
                conn.close()
            print(json.dumps(granted, ensure_ascii=False, indent=2))
        elif args.command == "mcp":
            from yuxu.mcp_server import run

            run()
        elif args.command == "status":
            print(json.dumps(server.status(), ensure_ascii=False, indent=2))
        elif args.command == "stop":
            print(json.dumps(server.stop(), ensure_ascii=False, indent=2))
        return 0
    except BusinessError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
