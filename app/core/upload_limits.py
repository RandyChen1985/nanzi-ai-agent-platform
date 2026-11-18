"""上传类硬上限的单一来源。

技能包（zip / tar.gz / tgz）与工作空间文件是两条独立链路：后者按
`WORKSPACE_UPLOAD_MAX_MB` 配置、流式落盘；技能包由管理员或普通用户手工导入，
一次性读进内存再解压，所以上限写死在这里，两个端点模块共用同一份数字。

放核心层而不是端点层，是为了让 `skills.py` 与 `personal_skills.py` 不必互相
导入（端点模块互相 import 容易绕出循环依赖）。
"""

import os

#: 技能包上传大小上限（MB）。100MB 足够装下一套带脚本、模板与少量资源的技能；
#: 再大更适合走工作空间或 Git 仓库，而不是塞进一次表单上传。
SKILL_PACKAGE_MAX_MB = 100

#: 同上，字节形式，供 `len(content) > ...` 直接比较。
SKILL_PACKAGE_MAX_BYTES = SKILL_PACKAGE_MAX_MB * 1024 * 1024

# ---------------------------------------------------------------------------
# 文件个数预算（单一来源）
# ---------------------------------------------------------------------------

#: 单个目录允许的文件总数上限，**全部链路共用这一个数字**。
#:
#: 口径是「目录累计」而不是「单次请求」：此前的实现只数本次归档/本次请求的条目数，
#: 于是同一个目录解两次 4000 条的包、或手工循环单文件上传，都能无限累计，上限形同虚设。
#:
#: 覆盖的入口：
#:
#: - 工作空间「解压到…」（目标目录已有文件 + 本次归档文件数）
#: - 工作空间「上传到此目录」（目标目录已有文件数 + 1）
#: - 技能/个人技能的压缩包上传与导入（技能目录已有文件 + 本次归档文件数）
#: - 技能/个人技能的单文件上传（此前完全没有校验，可循环突破）
#: - 模型侧 ``write_skill_file`` / ``import_skill_from_sandbox``（累计写入预算）
MAX_FILES_PER_DIRECTORY = 200_000

# ---------------------------------------------------------------------------
# 单个技能目录的写入预算
# ---------------------------------------------------------------------------

#: 单个技能目录内允许的文件总数与总字节数。
#:
#: 这两个数字原先写死在 ``app/api/portal/endpoints/skills.py``，只管「解压压缩包」这一个
#: 入口。但技能资产还有第二个写入口——模型侧的 ``write_skill_file`` 工具：它一次写一个
#: 文件，单次看永远不超标，循环几百次就能把目录灌满（inode 与磁盘都会遭殃）。
#: 因此把预算上移到核心层，解压与工具写入共用同一份口径。
#:
#: 文件数直接取 :data:`MAX_FILES_PER_DIRECTORY`——技能目录与工作空间目录不该有两套
#: 数字，否则「同一份预算」只是注释里的说法。
SKILL_DIR_MAX_FILES = MAX_FILES_PER_DIRECTORY

#: 500MB ≈ 上传上限（100MB）的 5 倍余量：正常技能包压缩率本来就高，卡太紧会误伤。
SKILL_DIR_MAX_BYTES = 500 * 1024 * 1024

#: 单个技能资产文件的大小上限，对齐 ``POST /personal-skills/{id}/upload`` 的 10MB。
SKILL_ASSET_MAX_FILE_MB = 10
SKILL_ASSET_MAX_FILE_BYTES = SKILL_ASSET_MAX_FILE_MB * 1024 * 1024


def count_files_under(root: str, *, stop_above: int | None = None) -> int:
    """统计目录树内的文件个数（不跟随符号链接），供「目录累计」口径使用。

    为什么带上 ``stop_above``：调用方通常只关心「是否已超上限」，而不关心精确到达
    二十几万。命中 ``stop_above`` 后立刻返回（返回值只保证 ``> stop_above``），
    避免每次上传都在一个已经很大的目录上白走一遍完整遍历。

    统计口径是**目录项里的非目录条目**：目录本身不计数；符号链接（含软链目录）一律
    跳过且不跟随；其余条目（普通文件、FIFO、socket 等）各计一个。实现用 ``os.scandir``
    迭代目录项，借系统提供的目录项类型信息避免对每个文件额外发起 ``lstat`` 系统调用。
    """
    limit = stop_above
    root_str = str(root or "")
    if not root_str or not os.path.isdir(root_str):
        return 0
    count = 0
    stack = [root_str]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            count += 1
                            if limit is not None and count > limit:
                                return count
                    except OSError:
                        continue
        except OSError:
            continue
    return count
