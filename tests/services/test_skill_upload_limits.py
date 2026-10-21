"""技能导入、解压、发布三处上限必须互相兼容。

导入放宽到 100MB 后，如果发布快照还卡在 20MB，就会出现「能导入、发不出去」：
包解压后目录一算总量直接 `skill snapshot exceeds 20MB`。同类问题还有解压预算——
上传 100MB 的包若解压上限不动，高压缩比归档会在解压中途被拦。

所以这里锁的是**横向一致性**，不是单个数字：发布单文件 ≥ 导入上限，发布总量与
解压预算都按解压后留余量。
"""

import pytest

from app.api.portal.endpoints import skills
from app.core import upload_limits
from app.services import skill_publication_service as publication

pytestmark = pytest.mark.no_infrastructure


def test_snapshot_limits_accommodate_the_package_limit():
    """发布/提交审核要复制整个技能目录，上限不能比导入上限还小。"""
    assert publication.MAX_FILE_SIZE >= upload_limits.SKILL_PACKAGE_MAX_BYTES
    assert publication.MAX_SNAPSHOT_SIZE >= 5 * upload_limits.SKILL_PACKAGE_MAX_BYTES


def test_extract_budget_leaves_room_for_bigger_packages():
    """压缩包越界放宽后，解压预算要跟着抬，否则解压到一半就被判 zip bomb。"""
    assert skills.MAX_EXTRACTED_BYTES >= 5 * upload_limits.SKILL_PACKAGE_MAX_BYTES
