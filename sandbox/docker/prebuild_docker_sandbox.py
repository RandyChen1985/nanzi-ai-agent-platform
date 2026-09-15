#!/usr/bin/env python3
"""NanZi AI · Docker 安全代码沙箱镜像预构建与管理工具 (Docker Sandbox Prebuild & Management Utility).

支持在宿主机或容器内部直接执行构建，支持配置 HTTP/HTTPS 代理、自定义基础镜像、实时查看 Docker 构建日志，
支持 --dry-run 演练生成 Dockerfile、--list 探测本地沙箱镜像，以及安全防误触交互确认。

使用方式:
  1. 默认交互式构建（无参数或带 -y）:
     ./sandbox/docker/build-docker-sandbox-image.sh
     ./sandbox/docker/build-docker-sandbox-image.sh -y

  2. 演练模式（仅生成 Dockerfile 与上下文，不触发构建）:
     ./sandbox/docker/build-docker-sandbox-image.sh --dry-run

  3. 探测本地所有已构建的沙箱镜像:
     ./sandbox/docker/build-docker-sandbox-image.sh --list

  4. 检查当前基础镜像的预构建状态与 Tag:
     ./sandbox/docker/build-docker-sandbox-image.sh --status

  5. 带代理构建 / 强制重新构建:
     ./sandbox/docker/build-docker-sandbox-image.sh --proxy http://127.0.0.1:7890
     ./sandbox/docker/build-docker-sandbox-image.sh --force --base-image python:3.11-slim
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 导入动态补丁确保排障工具已注入
from app.services.ai.runtime.agentscope.docker_template_patch import (
    EXTRA_SANDBOX_APT_PACKAGES,
    apply_agentscope_docker_patches,
)

apply_agentscope_docker_patches()

BANNER = """\
\033[1;36m======================================================================\033[0m
\033[1;36m       NanZi AI · Docker 安全代码沙箱镜像预构建与运维工具              \033[0m
\033[1;36m======================================================================\033[0m\
"""


def _format_size(size_bytes: int | None) -> str:
    """格式化字节大小为可读文本。"""
    if size_bytes is None:
        return "未知"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}TB"


async def check_status_cli(base_image: str | None) -> None:
    """查询 Docker 沙箱镜像预构建状态。"""
    from app.services.ai.runtime.agentscope.docker_prebuild import (
        DEFAULT_DOCKER_BASE_IMAGE,
        docker_workspace_prebuild_status,
    )

    effective_base = base_image or DEFAULT_DOCKER_BASE_IMAGE
    print("\n🔍 正在检查 Docker 沙箱镜像状态...")
    status = await docker_workspace_prebuild_status(base_image=effective_base)

    print("=" * 65)
    print(f"  基础镜像 (Base Image):     {effective_base}")
    print(f"  计算 Tag:                  {status.get('tag') or '无法计算'}")
    print(f"  内置排障工具链:            {', '.join(EXTRA_SANDBOX_APT_PACKAGES)}")
    print(
        f"  Docker Daemon 状态:        {'🟢 可连接' if status.get('docker_available') else '🔴 不可达'}"
    )
    print(
        f"  镜像预构建就绪 (Prebuilt): {'✅ 已就绪 (秒级拉起)' if status.get('prebuilt') else '⏳ 尚未预构建'}"
    )
    print(f"  状态说明:                  {status.get('message')}")
    print("=" * 65)


async def list_sandbox_images_cli(base_image: str | None = None) -> None:
    """探测本地 Docker daemon 中所有沙箱镜像 (agentscope-workspace:*)。"""
    try:
        import aiodocker
    except ImportError:
        print("❌ 未安装 aiodocker，无法查询 Docker 镜像！")
        sys.exit(1)

    from app.services.ai.runtime.agentscope.docker_prebuild import (
        DEFAULT_DOCKER_BASE_IMAGE,
        _try_prepare_context_tag,
        check_docker_daemon,
    )

    daemon_status = await check_docker_daemon(aiodocker)
    if not daemon_status["available"]:
        print(f"❌ 无法连接 Docker Daemon: {daemon_status.get('message')}")
        sys.exit(1)

    effective_base = base_image or DEFAULT_DOCKER_BASE_IMAGE
    current_tag = await _try_prepare_context_tag(effective_base)

    docker = aiodocker.Docker()
    try:
        images = await docker.images.list()
        sandbox_images: list[dict[str, Any]] = []

        for img in images:
            repo_tags = img.get("RepoTags") or []
            for rt in repo_tags:
                if rt.startswith("agentscope-workspace:"):
                    sandbox_images.append(
                        {
                            "repo_tag": rt,
                            "tag": rt.split(":", 1)[1] if ":" in rt else rt,
                            "id": (img.get("Id") or "").replace("sha256:", "")[:12],
                            "size": img.get("Size"),
                            "created": img.get("Created"),
                            "is_current": rt == current_tag,
                        }
                    )

        print("\n" + "=" * 80)
        print("📋 本地 Docker 沙箱镜像清单 (agentscope-workspace)")
        print(f"   当前配置期望 Tag: {current_tag or '计算中...'}")
        print("=" * 80)

        if not sandbox_images:
            print("⏳ 本地暂无任何 agentscope-workspace 沙箱镜像。")
            print("👉 您可以运行: ./sandbox/docker/build-docker-sandbox-image.sh 立即开始构建。\n")
            return

        header = f"{'REPOSITORY':<22} {'TAG':<16} {'IMAGE ID':<14} {'SIZE':<10} {'状态'}"
        print(header)
        print("-" * 80)
        for item in sandbox_images:
            repo = "agentscope-workspace"
            tag_display = item["tag"][:14]
            img_id = item["id"]
            size_str = _format_size(item["size"])
            status_str = "✅ 匹配当前配置 (已就绪)" if item["is_current"] else "⏳ 历史/其他版本"
            print(f"{repo:<22} {tag_display:<16} {img_id:<14} {size_str:<10} {status_str}")
        print("=" * 80 + "\n")

    finally:
        await docker.close()


async def dry_run_cli(base_image: str | None) -> None:
    """演练模式：生成构建上下文并打印 Dockerfile 全文与包含文件，不触发实际构建。"""
    from app.services.ai.runtime.agentscope.docker_prebuild import (
        DEFAULT_DOCKER_BASE_IMAGE,
        _prepare_context,
    )

    effective_base = (base_image or DEFAULT_DOCKER_BASE_IMAGE).strip()
    print("\n" + "=" * 70)
    print("🔍 [Dry-Run 演练模式] 生成 Docker 构建上下文 (不触发实际构建)")
    print("=" * 70)

    ctx_dir = None
    try:
        ctx_dir, tag = await _prepare_context(effective_base)
        print(f"  🎯 计算出的镜像 Tag:      {tag}")
        print(f"  📦 基础镜像 (Base Image): {effective_base}")
        print(f"  🛠️ 注入排障工具链:       {', '.join(EXTRA_SANDBOX_APT_PACKAGES)}")
        print(f"  📂 临时上下文目录:        {ctx_dir}")
        print("-" * 70)

        dockerfile_path = Path(ctx_dir) / "Dockerfile"
        if dockerfile_path.exists():
            print("📄 生成的 Dockerfile 完整内容:\n")
            for line in dockerfile_path.read_text(encoding="utf-8").splitlines():
                print(f"  │ {line}")
            print("-" * 70)

        print("📁 构建上下文包含的文件清单:")
        for item in Path(ctx_dir).iterdir():
            size_str = _format_size(item.stat().st_size)
            print(f"  - {item.name:<25} ({size_str})")

        print("=" * 70)
        print("💡 Dry-Run 演练完成！未向 Docker Daemon 发起任何构建任务。")
        print("👉 若需正式执行构建，请运行: ./sandbox/docker/build-docker-sandbox-image.sh\n")
    finally:
        if ctx_dir:
            shutil.rmtree(ctx_dir, ignore_errors=True)


async def prebuild_direct(
    base_image: str | None,
    force: bool = False,
    proxy: str | None = None,
) -> None:
    try:
        import aiodocker
    except ImportError:
        print("❌ 当前 Python 环境未安装 aiodocker 依赖！")
        print("👉 请先激活平台虚拟环境（source .venv/bin/activate）或执行: pip install aiodocker")
        sys.exit(1)

    from app.services.ai.runtime.agentscope.docker_prebuild import (
        DEFAULT_DOCKER_BASE_IMAGE,
        _image_exists,
        _mark_prebuilt,
        _prepare_context,
        check_docker_daemon,
    )

    effective_base = (base_image or DEFAULT_DOCKER_BASE_IMAGE).strip()
    print("\n" + "=" * 70)
    print("🚀 开始执行 Docker 沙箱镜像预构建")
    print(f"   - 基础镜像:     {effective_base}")
    print(f"   - 预装排障工具: {', '.join(EXTRA_SANDBOX_APT_PACKAGES)}")
    print(f"   - 强制重建:     {'是 (忽略缓存)' if force else '否 (命中缓存则秒级复用)'}")

    # 代理处理
    build_args: dict[str, str] = {}
    http_proxy = proxy or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_proxy = proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")

    if http_proxy:
        build_args["HTTP_PROXY"] = http_proxy
        build_args["http_proxy"] = http_proxy
        print(f"   - HTTP 代理:    {http_proxy}")
    if https_proxy:
        build_args["HTTPS_PROXY"] = https_proxy
        build_args["https_proxy"] = https_proxy
        print(f"   - HTTPS 代理:   {https_proxy}")
    if no_proxy:
        build_args["NO_PROXY"] = no_proxy
        build_args["no_proxy"] = no_proxy
        print(f"   - NO_PROXY:     {no_proxy}")
    print("=" * 70 + "\n")

    # 1. 检查 Docker daemon
    daemon_status = await check_docker_daemon(aiodocker)
    if not daemon_status["available"]:
        print(f"❌ 无法连接 Docker Daemon: {daemon_status.get('message')}")
        if daemon_status.get("error"):
            print(f"   详细错误: {daemon_status['error']}")
        sys.exit(1)

    print("🟢 Docker Daemon 连接正常")

    # 2. 生成构建上下文
    ctx_dir: str | None = None
    client: Any | None = None
    try:
        print("📦 正在生成 AgentScope Docker 构建上下文 (含排障工具链)...")
        ctx_dir, tag = await _prepare_context(effective_base)
        print(f"🎯 目标构建 Tag: {tag}")
        print(f"📂 临时上下文目录: {ctx_dir}")

        client = aiodocker.Docker()

        # 3. 检查缓存
        if not force and await _image_exists(client, tag):
            print(f"\n✨ 镜像缓存命中！镜像 [{tag}] 已存在于本地 Docker 中，无需重新构建。")
            await _mark_prebuilt(effective_base)
            print("✅ 已同步将预构建完成状态写入系统配置表。")
            print("👉 前端【系统设置】→【参数配置】→【沙箱配置】已自动就绪。\n")
            return

        # 4. 打包构建上下文
        print("🗜️ 正在压缩打包构建上下文 (tar)...")
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tf:
            tf.add(ctx_dir, arcname=".")
        tar_buf.seek(0)

        # 5. 执行构建并流式输出日志
        print("🔨 正在向 Docker Daemon 提交构建任务，请等待（日志实时输出中）...\n" + "-" * 70)

        build_params: dict[str, Any] = {
            "fileobj": tar_buf,
            "tag": tag,
            "stream": True,
            "rm": True,
            "encoding": "identity",
        }
        if build_args:
            build_params["buildargs"] = build_args

        async for chunk in client.images.build(**build_params):
            if isinstance(chunk, dict):
                stream_text = chunk.get("stream") or chunk.get("status")
                if stream_text and isinstance(stream_text, str):
                    sys.stdout.write(stream_text)
                    sys.stdout.flush()
                error = chunk.get("error") or chunk.get("errorDetail")
                if error:
                    msg = error.get("message") if isinstance(error, dict) else str(error)
                    print(f"\n❌ Docker 构建失败: {msg}")
                    sys.exit(1)

        print("-" * 70)
        print(f"\n🎉 镜像 [{tag}] 构建成功！已具备完整排障与系统命令能力。")

        # 6. 标记完成并持久化
        print("💾 正在更新系统配置与预构建状态标记...")
        await _mark_prebuilt(effective_base)
        print("✅ 状态已成功写入平台数据库和 Redis 缓存！")
        print("👉 前端【系统设置】→【参数配置】→【沙箱配置】已自动就绪，智能体会话将秒级拉起沙箱容器。\n")

    except Exception as exc:
        print(f"\n❌ 构建过程中发生异常: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        if ctx_dir:
            shutil.rmtree(ctx_dir, ignore_errors=True)
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def trigger_via_http(
    api_url: str,
    api_key: str,
    base_image: str | None = None,
) -> None:
    """通过平台管理接口触发预构建并打印结果。"""
    import json
    import urllib.error
    import urllib.request

    endpoint = f"{api_url.rstrip('/')}/api/v1/admin/sandbox/docker/prebuild"
    payload = {}
    if base_image:
        payload["base_image"] = base_image

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
        method="POST",
    )

    print(f"\n🌐 正在向管理接口发起预构建请求: {endpoint}")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("=" * 60)
            print(f"  返回码:   {data.get('code')}")
            print(f"  消息:     {data.get('message')}")
            if "data" in data and isinstance(data["data"], dict):
                sub = data["data"]
                print(f"  Tag:      {sub.get('tag')}")
                print(f"  状态:     {'已就绪' if sub.get('prebuilt') else '未就绪'}")
            print("=" * 60)
            if data.get("code") == 200:
                print("\n🎉 通过 HTTP 接口触发预构建成功！")
            else:
                print(f"\n⚠️ 接口返回非 200 结果: {data.get('message')}")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"❌ HTTP 请求失败: {exc.code} {exc.reason}\n{err_body}")
        sys.exit(1)
    except Exception as exc:
        print(f"❌ 请求异常: {exc}")
        sys.exit(1)


def main() -> None:
    print(BANNER)
    parser = argparse.ArgumentParser(
        description="NanZi AI · Docker 安全沙箱镜像预构建与管理运维工具",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="演练模式：仅生成并打印包含排障工具的 Dockerfile 与构建上下文，不调用 Docker API",
    )
    parser.add_argument(
        "-l",
        "--list",
        "--check",
        dest="list_images",
        action="store_true",
        help="探测并列出本地 Docker 中所有 agentscope-workspace 沙箱镜像及匹配状态",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="免交互确认直接执行构建 (适合自动化脚本或无头环境)",
    )
    parser.add_argument(
        "--base-image",
        type=str,
        default="python:3.11-slim",
        help="指定 Docker 基础镜像 (默认 python:3.11-slim，可指定内部/加速镜像)",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="指定构建时使用的 HTTP/HTTPS 代理 (如 http://127.0.0.1:7890)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新构建（忽略本地已存在的镜像缓存）",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="仅检查当前基础镜像的预构建状态与 Tag，不执行构建",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=None,
        help="通过平台的 HTTP API 触发预构建 (例如 http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="通过 HTTP API 触发时附带的管理员 API Key (X-API-Key)",
    )

    # 兼容 positional 参数输入 list
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        sys.argv[1] = "--list"

    args = parser.parse_args()

    if args.dry_run:
        asyncio.run(dry_run_cli(base_image=args.base_image))
    elif args.list_images:
        asyncio.run(list_sandbox_images_cli(base_image=args.base_image))
    elif args.status:
        asyncio.run(check_status_cli(base_image=args.base_image))
    elif args.api_url:
        if not args.api_key:
            print("❌ 使用 --api-url 模式时必须通过 --api-key 提供管理员 API Key！")
            sys.exit(1)
        asyncio.run(
            trigger_via_http(
                api_url=args.api_url,
                api_key=args.api_key,
                base_image=args.base_image,
            )
        )
    else:
        # 无参且处于交互式终端时，进行安全防误触提示
        is_interactive = sys.stdin.isatty()
        if is_interactive and not args.yes and len(sys.argv) <= 1:
            print(f"💡 未指定参数，当前默认基础镜像: [{args.base_image}]")
            print(f"   已内置排障工具: {', '.join(EXTRA_SANDBOX_APT_PACKAGES)}")
            print("   常用操作:")
            print("     - 查看参数帮助:       ./sandbox/docker/build-docker-sandbox-image.sh --help")
            print("     - 演练预览 Dockerfile: ./sandbox/docker/build-docker-sandbox-image.sh --dry-run")
            print("     - 查看本地已有镜像:   ./sandbox/docker/build-docker-sandbox-image.sh --list")
            print("----------------------------------------------------------------------")
            try:
                confirm = input("是否以默认配置立即开始构建？[y/N]: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\n\n已安全取消。")
                sys.exit(0)

            if confirm not in ("y", "yes"):
                print("已安全取消。若需免交互直接构建，可添加 -y 参数。")
                sys.exit(0)

        asyncio.run(
            prebuild_direct(
                base_image=args.base_image,
                force=args.force,
                proxy=args.proxy,
            )
        )


if __name__ == "__main__":
    main()
