"""工作空间压缩包解压。

只做「把一个压缩包安全地解到某个目录」这一件事，不关心 HTTP 与权限：

- 权限（源可读 / 目标可写）由 ``app/api/v1/endpoints/fs.py`` 在路由层收口；
- 这里负责格式识别、路径穿越防护、压缩炸弹配额、失败回滚。

刻意不引入第三方解压库：``zipfile`` / ``tarfile`` 足够覆盖产品支持的格式，
而 rar/7z 需要额外二进制依赖，收益不抵维护成本（前端会置灰并提示转换）。
"""

from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List

# 解压后总字节数上限与条目数上限的兜底默认值（路由层会用 settings 覆盖）。
DEFAULT_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 5000

# 顺序有意义：先匹配两级后缀，避免 ".tar.gz" 被当成 ".gz" 之类的误判。
SUPPORTED_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")
UNSUPPORTED_SUFFIXES = (".rar", ".7z", ".bz2", ".xz", ".zst")
_CHUNK_BYTES = 1024 * 1024


class ArchiveExtractError(Exception):
    """带 HTTP 语义的解压失败；路由层据此直接抛 ``HTTPException``。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ArchiveExtractResult:
    path: str
    file_count: int
    total_bytes: int
    skipped: List[Dict[str, str]] = field(default_factory=list)


def archive_suffix(path: str) -> str:
    """返回命中的受支持后缀（小写），不支持时返回空串。"""
    lower = str(path or "").lower()
    for suffix in SUPPORTED_SUFFIXES:
        if lower.endswith(suffix):
            return suffix
    return ""


def is_supported_archive(path: str) -> bool:
    return bool(archive_suffix(path))


def archive_stem(path: str) -> str:
    """压缩包名去掉后缀，作为默认子文件夹名；``.tar.gz`` 会去掉两层。"""
    base = os.path.basename(str(path or ""))
    suffix = archive_suffix(base)
    if suffix:
        return base[: -len(suffix)]
    return os.path.splitext(base)[0]


def extract_archive(
    archive_path: str,
    target_dir: str,
    subdir: str | None = None,
    *,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> ArchiveExtractResult:
    """把 ``archive_path`` 解到 ``target_dir``（或其中的 ``subdir`` 子目录）。

    ``subdir`` 为 ``None`` / 空串时直接解到 ``target_dir`` 本身；同名子目录已存在
    时自动追加 ``_1``、``_2``，不覆盖也不合并。任何失败都会回滚本次写出的内容。
    """
    source = os.path.abspath(str(archive_path or ""))
    if not os.path.isfile(source):
        raise ArchiveExtractError("压缩包不存在或不是文件。", 404)
    suffix = archive_suffix(source)
    if not suffix:
        if str(source).lower().endswith(UNSUPPORTED_SUFFIXES):
            raise ArchiveExtractError("暂不支持 rar/7z 等格式，请先转换成 zip。", 400)
        raise ArchiveExtractError("不支持的压缩格式，仅支持 zip / tar / tar.gz / tgz。", 400)

    target_root = os.path.abspath(str(target_dir or ""))
    if not os.path.isdir(target_root):
        raise ArchiveExtractError("目标路径不是目录。", 400)

    # None = 调用方没指定，落默认同名子文件夹；空串 = 用户显式清空，直接解到目标目录。
    if subdir is None:
        desired_name = _safe_subdir_name(archive_stem(source))
    elif not str(subdir).strip():
        desired_name = ""
    else:
        desired_name = _safe_subdir_name(str(subdir))
    created_final_dir = False
    if desired_name:
        final_dir = os.path.join(target_root, desired_name)
        if os.path.lexists(final_dir):
            final_dir = _next_available_dir(target_root, desired_name)
        os.makedirs(final_dir, exist_ok=False)
        created_final_dir = True
    else:
        final_dir = target_root

    state = _ExtractState(
        final_dir=final_dir,
        max_total_bytes=max(1, int(max_total_bytes)),
        max_entries=max(1, int(max_entries)),
    )
    try:
        if suffix == ".zip":
            _extract_zip(source, state)
        else:
            _extract_tar(source, state)
    except ArchiveExtractError:
        _rollback(state, created_final_dir)
        raise
    except Exception as exc:  # 损坏的包、编码异常等
        _rollback(state, created_final_dir)
        raise ArchiveExtractError(f"解压失败：{exc}", 500) from exc

    return ArchiveExtractResult(
        path=final_dir,
        file_count=state.file_count,
        total_bytes=state.total_bytes,
        skipped=state.skipped,
    )


@dataclass
class _ExtractState:
    final_dir: str
    max_total_bytes: int
    max_entries: int
    file_count: int = 0
    total_bytes: int = 0
    skipped: List[Dict[str, str]] = field(default_factory=list)
    written_paths: List[str] = field(default_factory=list)
    created_dirs: List[str] = field(default_factory=list)

    @property
    def remaining_bytes(self) -> int:
        return self.max_total_bytes - self.total_bytes

    def skip(self, name: str, reason: str) -> None:
        self.skipped.append({"name": name, "reason": reason})


def _safe_subdir_name(raw: str | None) -> str:
    """子文件夹只能是单层目录名；把路径分隔符与 ``..`` 挡在外面。"""
    name = str(raw or "").strip().replace("\\", "/")
    if not name:
        return ""
    if "/" in name or name in {".", ".."}:
        raise ArchiveExtractError("子文件夹名不合法，不能包含路径分隔符。", 400)
    return name


def _next_available_dir(root: str, name: str) -> str:
    for index in range(1, 100):
        candidate = os.path.join(root, f"{name}_{index}")
        if not os.path.lexists(candidate):
            return candidate
    raise ArchiveExtractError("同名目录过多，请先清理目标位置。", 409)


def _member_target(final_dir: str, member_name: str) -> str:
    """把成员名映射到最终路径，越界（zip slip）直接拒绝。"""
    normalized = str(member_name or "").replace("\\", "/")
    if not normalized:
        raise ArchiveExtractError("压缩包包含空成员名。", 400)
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise ArchiveExtractError(f"压缩包包含绝对路径成员：{member_name}", 400)
    root = os.path.normpath(final_dir)
    target = os.path.normpath(os.path.join(root, normalized))
    if target != root and not target.startswith(root + os.sep):
        raise ArchiveExtractError(f"压缩包包含越界路径成员：{member_name}", 400)

    # 字符串路径落在界内还不够：界内可能早就有符号链接（用户或别的工具建的），
    # 沿它写下去就出了包外。对最近一个已存在的祖先做 realpath 校验。
    real_root = os.path.realpath(root)
    ancestor = os.path.dirname(target)
    while ancestor and not os.path.isdir(ancestor):
        parent = os.path.dirname(ancestor)
        if parent == ancestor:
            ancestor = ""
            break
        ancestor = parent
    if ancestor:
        real_ancestor = os.path.realpath(ancestor)
        if real_ancestor != real_root and not real_ancestor.startswith(real_root + os.sep):
            raise ArchiveExtractError(
                f"压缩包成员会写入符号链接目录，已拒绝：{member_name}", 400
            )
    return target


def _ensure_dir(path: str, created: List[str]) -> None:
    """逐层建目录并记录新建的层级，供失败时回滚。"""
    if not path or path == os.path.dirname(path):
        return
    missing: List[str] = []
    current = path
    while current and current != os.path.dirname(current):
        if os.path.isdir(current):
            break
        missing.append(current)
        current = os.path.dirname(current)
    for item in reversed(missing):
        os.makedirs(item, exist_ok=True)
        created.append(item)


def _rollback(state: _ExtractState, created_final_dir: bool) -> None:
    if created_final_dir:
        shutil.rmtree(state.final_dir, ignore_errors=True)
        return
    # 直接解到既有目录时只删本次写出的文件，再尽力删掉本次新建的空目录。
    for path in reversed(state.written_paths):
        try:
            os.remove(path)
        except OSError:
            pass
    for directory in reversed(state.created_dirs):
        try:
            os.rmdir(directory)
        except OSError:
            pass


def _copy_limited(source, target: str, state: _ExtractState) -> int:
    """边写边计数：成员声明大小可以伪造，实际写出量才算数。"""
    written = 0
    try:
        with open(target, "xb") as handle:
            while True:
                chunk = source.read(_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > state.remaining_bytes:
                    handle.close()
                    raise ArchiveExtractError(
                        "解压后体积超过上限，已中止并回滚。", 413
                    )
                handle.write(chunk)
    except FileExistsError as exc:
        raise ArchiveExtractError("目标位置存在同名文件。", 409) from exc
    return written


def _zip_member_filename(info: zipfile.ZipInfo) -> str:
    """zip 文件名字节流解码。

    ZIP 规范里只有设置了 UTF-8 flag（0x800）才是 UTF-8；否则 zipfile 按 cp437
    解码。国内打包工具常写 GBK 却不设 flag，这里按 cp437 → gbk 回退还原。
    """
    name = info.filename
    if not name or (info.flag_bits & 0x800):
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    try:
        decoded = raw.decode("gbk")
    except UnicodeDecodeError:
        return name
    return decoded or name


def _extract_zip(archive: str, state: _ExtractState) -> None:
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > state.max_entries:
            raise ArchiveExtractError(
                f"压缩包条目数超过上限（{state.max_entries}），已中止并回滚。", 413
            )
        declared = sum(max(info.file_size, 0) for info in infos if not info.is_dir())
        if declared > state.max_total_bytes:
            raise ArchiveExtractError("解压后体积超过上限，已中止并回滚。", 413)

        for info in infos:
            name = _zip_member_filename(info)
            if not name:
                continue
            # 符号链接：高 16 位是 unix mode，S_IFLNK 标记
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                state.skip(name, "not_regular_file")
                continue
            target = _member_target(state.final_dir, name)
            if info.is_dir() or name.endswith("/"):
                _ensure_dir(target, state.created_dirs)
                continue
            if os.path.lexists(target):
                state.skip(name, "exists")
                continue
            _ensure_dir(os.path.dirname(target), state.created_dirs)
            with zf.open(info) as source:
                written = _copy_limited(source, target, state)
            state.written_paths.append(target)
            state.file_count += 1
            state.total_bytes += written


def _extract_tar(archive: str, state: _ExtractState) -> None:
    with tarfile.open(archive, "r:*") as tf:
        members = tf.getmembers()
        if len(members) > state.max_entries:
            raise ArchiveExtractError(
                f"压缩包条目数超过上限（{state.max_entries}），已中止并回滚。", 413
            )
        declared = sum(max(member.size, 0) for member in members if member.isfile())
        if declared > state.max_total_bytes:
            raise ArchiveExtractError("解压后体积超过上限，已中止并回滚。", 413)

        for member in members:
            if member.isdir():
                target = _member_target(state.final_dir, member.name)
                _ensure_dir(target, state.created_dirs)
                continue
            # 符号链接 / 硬链接 / 设备文件一律不解：它们能把写入引到包外。
            if not member.isfile():
                state.skip(member.name, "not_regular_file")
                continue
            target = _member_target(state.final_dir, member.name)
            if os.path.lexists(target):
                state.skip(member.name, "exists")
                continue
            _ensure_dir(os.path.dirname(target), state.created_dirs)
            source = tf.extractfile(member)
            if source is None:
                state.skip(member.name, "unreadable")
                continue
            with source:
                written = _copy_limited(source, target, state)
            state.written_paths.append(target)
            state.file_count += 1
            state.total_bytes += written


def extract_limits_from_settings() -> tuple[int, int]:
    """从平台配置读取解压配额；配置缺失或非法时回退默认值。"""
    try:
        from app.core.config import settings

        megabytes = int(getattr(settings, "WORKSPACE_EXTRACT_MAX_MB", 0) or 0)
        entries = int(getattr(settings, "WORKSPACE_EXTRACT_MAX_ENTRIES", 0) or 0)
    except Exception:  # pragma: no cover - 配置不可用时保持接口可用
        return DEFAULT_MAX_TOTAL_BYTES, DEFAULT_MAX_ENTRIES
    max_bytes = megabytes * 1024 * 1024 if megabytes > 0 else DEFAULT_MAX_TOTAL_BYTES
    max_entries = entries if entries > 0 else DEFAULT_MAX_ENTRIES
    return max_bytes, max_entries


def skipped_to_payload(result: ArchiveExtractResult) -> List[Dict[str, Any]]:
    """给响应层用的浅拷贝，避免调用方改动模块内部结构。"""
    return [dict(item) for item in result.skipped]
