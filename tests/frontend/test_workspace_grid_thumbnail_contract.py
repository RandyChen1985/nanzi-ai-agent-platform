"""契约：工作空间卡片视图里，图片显示真实缩略图，而不是同一个通用图标。

背景（用户反馈）：`generated/` 目录里堆着生成的图片，卡片视图里每一张都长一样——
`fileEmoji()` 只按扩展名返回 emoji（图片一律 🖼️），这块从来没接过 `<img>`。
用户看到的是 20 个一模一样的图标，分不清哪张是哪张。

为什么现在是原图直链而不是缩略图接口：当前环境没装 Pillow（`requirements.txt`
里也没有），后端生成缩略图要新增依赖并重建镜像。`/api/v1/chat/fs/preview`
本来就会返回原文件，配上 `loading="lazy"` 后只拉可见卡片——该目录 20 张图约 30MB，
首屏约 15MB。换来的是零依赖、当天可用；真要压到几百 KB 再上后端缩略图。

这几条锁住的是：出图、只对白名单类型出图、失败能退回去、卡片高度不被撑乱。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/WorkspaceManage.vue"


def _read() -> str:
    return PAGE.read_text(encoding="utf-8")


def _grid_block(source: str) -> str:
    """卡片视图的模板片段：从 grid 容器到它的收尾，避免命中列表视图。"""
    start = source.index('class="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6"')
    return source[start : start + 2600]


def test_grid_card_renders_real_thumbnail_for_images():
    grid = _grid_block(_read())
    assert "<img" in grid, "卡片视图没有渲染图片"
    # 光有 <img> 不够：v-if 条件被改成 false 时它就变成死代码，而标签仍在
    assert 'v-if="isImageItem(item) && !thumbFailed[item.path]"' in grid
    assert ':src="resolveFsPreviewUrl(item.path)"' in grid
    # 缩略图必须在媒体区里满铺并裁切，否则会被压扁或留白
    assert "object-cover" in grid


def test_thumbnail_is_lazy_and_async():
    """30MB 的目录不能一进页面就全量下载。"""
    grid = _grid_block(_read())
    assert 'loading="lazy"' in grid
    assert 'decoding="async"' in grid


def test_thumbnail_falls_back_to_emoji_on_error():
    """坏文件 / 被移走的文件 / 越权路径都会让 preview 报错，不能留一块空白。"""
    source = _read()
    grid = _grid_block(source)
    assert '@error="onThumbError(item.path)"' in grid
    assert "!thumbFailed[item.path]" in grid
    assert "const onThumbError = (path: string)" in source
    # 回退分支保留 emoji（文件夹与其它类型都靠它）
    assert 'fileEmoji(item.name)' in grid


def test_only_previewable_extensions_get_a_thumbnail():
    """用 preview 的白名单，而不是 `fileEmoji` 里更宽的那张表。

    `fileEmoji` 把 svg 也算图片，但后端 `IMAGE_PREVIEW_EXTENSIONS` 里没有 svg——
    给 svg 出图只会换来一串注定失败的请求，最后全部退化成 emoji。
    """
    source = _read()
    assert "IMAGE_EXTENSIONS.has(`.${extOf(item.name)}`)" in source
    assert "!item.is_dir" in source, "文件夹不能出图"
    # 白名单本身必须与后端一致（不含 svg）
    formats = (ROOT / "frontend/src/utils/documentPreviewFormats.ts").read_text(encoding="utf-8")
    image_exts = re.search(r"IMAGE_EXTENSIONS = new Set\(\[(.+?)\]\)", formats, re.S)
    assert image_exts, "找不到 IMAGE_EXTENSIONS 定义"
    assert "svg" not in image_exts.group(1).lower()


def test_media_area_keeps_card_heights_uniform():
    """大图相册风：图片、文件夹与文档共用同一个 aspect-[4/3] 满宽媒体区，保证同一行卡片等高。"""
    grid = _grid_block(_read())
    assert "aspect-[4/3] w-full" in grid
    # emoji 走同一个媒体区，不再单独占一行
    media = re.search(r'class="relative flex aspect-\[4/3\].*?</div>', grid, re.S)
    assert media, "找不到媒体区容器"
    assert 'fileEmoji(item.name)' in media.group(0)
