<script setup lang="ts">
/**
 * 页面标题卡底部的背景剪影。
 *
 * 为什么抽成组件：知识库、数据源、元数据、案例集四个页头用的是同一套配方
 * （白卡 + 右上极淡蓝光晕 + 底部一条 currentColor 剪影 + 两端渐隐），
 * 只有"画什么"不同。四份各自维护一份伪随机生成器与图元函数，改一处就得改四处。
 *
 * 图形全部**生成**而非手写：手写几十个 <rect> 一改布局就要重排，生成只需调区间；
 * 种子写死，所以每次渲染形状完全一致，不会"跳动"。
 *
 * 颜色交给 currentColor，不透明度固定 0.11 —— 只为在白色卡片上"读得出轮廓"，不抢正文。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** books=书脊(知识库) | racks=机柜塔(数据源) | fields=字段矩阵(元数据) | cards=案例卡(案例集) */
    variant: 'books' | 'racks' | 'fields' | 'cards'
    /** 桌面端剪影高度（px）。窄屏统一压到 34px，避免压住副标题那一行 */
    height?: number
  }>(),
  { height: 46 },
)

/** 一个剪影图元。只用到 rect / path 两种，够画这四款图形 */
type Shape =
  | { tag: 'rect'; attrs: Record<string, string | number> }
  | { tag: 'path'; attrs: Record<string, string | number> }

function shapeRect(x: number, y: number, w: number, h: number, r = 0): Shape {
  const attrs: Record<string, string | number> = { x, y, width: w, height: h }
  if (r > 0) attrs.rx = r
  return { tag: 'rect', attrs }
}

/** evenodd 复合路径：外形 + 若干"挖空"块 */
function shapeCut(outline: string, holes: string[]): Shape {
  return {
    tag: 'path',
    attrs: {
      'fill-rule': 'evenodd',
      d: `${outline}${holes.length ? ' ' + holes.join(' ') : ''}`,
    },
  }
}

/** 画布固定 1400×80，地面线 y=74：剪影贴着卡片下沿，留 6 单位做"托板/落地" */
const ART_W = 1400
const FLOOR = 74

/** 32 位线性同余：Math.imul 才是 32 位整数乘法，直接相乘会超出 2^53 让序列失真 */
function lcg(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (Math.imul(s, 1103515245) + 12345) & 0x7fffffff
    return s / 2147483648
  }
}

/** 圆角矩形**路径**（用于拼进 evenodd 复合路径）；r=0 时退化成直角矩形 */
function rectPath(x: number, y: number, w: number, h: number, r = 0): string {
  const rr = Math.min(r, w / 2, h / 2)
  if (rr <= 0) return `M${x},${y} h${w} v${h} h${-w} z`
  return (
    `M${x + rr},${y} h${w - 2 * rr} a${rr},${rr} 0 0 1 ${rr},${rr} v${h - 2 * rr} ` +
    `a${rr},${rr} 0 0 1 ${-rr},${rr} h${-(w - 2 * rr)} a${rr},${rr} 0 0 1 ${-rr},${-rr} ` +
    `v${-(h - 2 * rr)} a${rr},${rr} 0 0 1 ${rr},${-rr} z`
  )
}

/**
 * 挖空是这套剪影唯一的"结构表达"手段：单色实心块画不出表格线、散热缝、正文行，
 * 只能把内部区域从外形里抠掉，露出卡片底色，读者才会把它看成有内容的卡片/机柜。
 */
function floorPlate(): Shape {
  return shapeRect(0, FLOOR, ART_W, 6, 3)
}

/* ---------------- 知识库：一排高低错落的书脊，偶尔一本斜靠 ---------------- */
function artBooks(): Shape[] {
  const rand = lcg(20261030)
  const out: Shape[] = []
  let x = 2
  while (x < ART_W) {
    const w = 8 + Math.round(rand() * 10)
    const roll = rand()
    // 高度刻意收敛在 [28,34]：差异放开就成了城市天际线，读不出是书
    const top =
      roll > 0.88 ? 58 + Math.round(rand() * 10)
      : roll > 0.78 ? 40 + Math.round(rand() * 8)
      : 28 + Math.round(rand() * 6)
    const tilt = rand() < 0.06 ? (rand() < 0.5 ? -11 : 12) : 0
    const spine = shapeRect(x, top, w, FLOOR - top, 2)
    if (tilt) spine.attrs.transform = `rotate(${tilt} ${x + w / 2} ${FLOOR})`
    out.push(spine)
    x += w + 1 + Math.round(rand() * 2)
  }
  out.push(floorPlate())
  return out
}

/* ---------------- 数据源：服务器机柜塔，帽檐 + 散热缝 ---------------- */
function artRacks(): Shape[] {
  const r = lcg(31415)
  const out: Shape[] = []
  let x = 6
  while (x < ART_W - 44) {
    const w = 26 + Math.round(r() * 16)
    const h = 30 + Math.round(r() * 26)
    const y = FLOOR - h
    const rows = 2 + Math.round(r() * 2)
    const slots: string[] = []
    for (let i = 0; i < rows; i += 1) {
      slots.push(rectPath(x + 5, y + 8 + (i * (h - 13)) / rows, w - 10, 2.6, 1.3))
    }
    out.push(shapeCut(rectPath(x, y, w, h, 2), slots))
    // 帽檐单独画：并进 evenodd 复合路径会被互挖成空心
    out.push(shapeRect(x + w / 2 - 3.5, y - 5, 7, 5, 1.5))
    x += w + 8 + Math.round(r() * 10)
  }
  out.push(floorPlate())
  return out
}

/* ---------------- 元数据：字段矩阵，按列堆叠小方块、底边对齐 ---------------- */
function artFields(): Shape[] {
  const r = lcg(90210)
  const out: Shape[] = []
  const bw = 12
  const bh = 7
  const gx = 4
  const gy = 3.5
  const maxRows = 5
  let x = 6
  while (x < ART_W - 20) {
    const rows = 1 + Math.round(r() * maxRows)
    for (let i = 0; i < rows; i += 1) {
      out.push(shapeRect(x, FLOOR - (i + 1) * bh - i * gy, bw, bh, 1.5))
    }
    x += bw + gx
  }
  return out
}

/* ---------------- 案例集：案例卡，右上折角 + 左图右文 ---------------- */
function artCards(): Shape[] {
  const r = lcg(778899)
  const out: Shape[] = []
  let x = 8
  while (x < ART_W - 60) {
    const w = 52 + Math.round(r() * 26)
    const h = 28 + Math.round(r() * 16)
    // 折角边长：小于 10 时在这个缩放下几乎看不见，又糊回一排书脊
    const fold = 13
    const y = FLOOR - h - (r() < 0.3 ? Math.round(r() * 3) : 0)
    // 外形右上角切掉一个 fold×fold 的三角
    const outline = `M${x},${y} h${w - fold} l${fold},${fold} v${h - fold} h${-w} z`
    // 左边缩略图占位 + 右边正文行（行数随卡片高度变化，避免矮卡里塞三行）
    const holes: string[] = [
      rectPath(x + 7, y + 8, 15, h - 16, 2),
      rectPath(x + 27, y + 10, w - 37, 3, 1.4),
    ]
    if (h > 32) holes.push(rectPath(x + 27, y + 17.5, (w - 37) * 0.7, 3, 1.4))
    if (h > 40) holes.push(rectPath(x + 27, y + 25, (w - 37) * 0.45, 3, 1.4))
    out.push(shapeCut(outline, holes))
    x += w + 10 + Math.round(r() * 12)
  }
  return out
}

const BUILDERS = {
  books: artBooks,
  racks: artRacks,
  fields: artFields,
  cards: artCards,
}

const shapes = computed(() => BUILDERS[props.variant]())
</script>

<template>
  <!--
    pointer-events-none + aria-hidden —— 纯装饰，不接事件也不进无障碍树。
    preserveAspectRatio="xMidYMax slice" —— 宽度变化时只裁左右、始终贴住下沿。
  -->
  <svg
    class="hero-silhouette"
    :style="{ '--hero-art-h': `${height}px` }"
    viewBox="0 0 1400 80"
    preserveAspectRatio="xMidYMax slice"
    fill="none"
    aria-hidden="true"
  >
    <g fill="currentColor" shape-rendering="geometricPrecision">
      <template v-for="(shape, i) in shapes" :key="i">
        <rect v-if="shape.tag === 'rect'" v-bind="shape.attrs" />
        <path v-else v-bind="shape.attrs" />
      </template>
    </g>
  </svg>
</template>

<style scoped>
.hero-silhouette {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1;
  width: 100%;
  height: var(--hero-art-h, 46px);
  color: #1677ff;
  opacity: 0.11;
  pointer-events: none;
  /* 卡片圆角是 rounded-2xl(16px)；父级不裁切（否则会切掉卡内的悬浮 tooltip），
     改由剪影自己切掉底部两个圆角，避免直角色块盖到卡片圆角外 */
  clip-path: inset(0 round 0 0 16px 16px);
  /* 两端渐隐，避免图形在卡片左右直角处被硬切 */
  -webkit-mask-image: linear-gradient(90deg, transparent 0%, #000 6%, #000 94%, transparent 100%);
  mask-image: linear-gradient(90deg, transparent 0%, #000 6%, #000 94%, transparent 100%);
}

/* 窄屏卡片变矮、内容变窄，剪影压到 34px 免得压住副标题 */
@media (max-width: 640px) {
  .hero-silhouette {
    height: min(var(--hero-art-h, 46px), 34px);
    opacity: 0.09;
  }
}
</style>
