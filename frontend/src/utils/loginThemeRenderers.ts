import type { LoginThemeId } from '@/constants/loginThemes'

export interface F3SatelliteItem {
  name: string
  label: string
  title: string
  desc: string
  orbit: number
  speed: number
  phase: number
}

export const F3_SATELLITES: readonly F3SatelliteItem[] = [
  {
    name: 'ROUTER',
    label: '01 · Router 编排',
    title: '多智能体意图编排与委派',
    desc: '自动识别复杂任务类型，拆解为并行执行计划并委派给领域专家智能体，全过程轨迹实时可见。',
    orbit: 0.42,
    speed: 0.00048,
    phase: 0.4,
  },
  {
    name: 'CHATBI',
    label: '02 · ChatBI 查数',
    title: '受控权限下的自然语言分析',
    desc: '直连业务数仓与关系库，在行级权限与脱敏策略保护下即时生成 SQL、可视化图表与归因结论。',
    orbit: 0.64,
    speed: -0.00036,
    phase: 2.1,
  },
  {
    name: 'RAG',
    label: '03 · 知识库 RAG',
    title: '引用级可溯源企业知识库',
    desc: '结合文档结构切片与混合检索，每条答复附带原文出处定位，让知识问答真正可用于企业决策。',
    orbit: 0.86,
    speed: 0.00028,
    phase: 3.9,
  },
  {
    name: 'MCP',
    label: '04 · MCP 工具链',
    title: '开放协议连接内部业务系统',
    desc: '通过标准化 MCP 工具集与托管凭据安全接驳工单、监控、IM 与研发系统，实现从问答到执行。',
    orbit: 1.08,
    speed: -0.00022,
    phase: 5.2,
  },
] as const

interface AstrolabePalette {
  glow0: string
  glow1: string
  axis: string
  ring: string
  ringAccent: string
  tickMajor: string
  tickMinor: string
  dot: string
  dotAccent: string
}

const DARK_PALETTE: AstrolabePalette = {
  glow0: 'rgba(37, 99, 235, 0.17)',
  glow1: 'rgba(14, 165, 233, 0.05)',
  axis: 'rgba(148, 163, 184, 0.09)',
  ring: 'rgba(148, 163, 184, 0.15)',
  ringAccent: 'rgba(56, 189, 248, 0.32)',
  tickMajor: 'rgba(125, 211, 252, 0.44)',
  tickMinor: 'rgba(148, 163, 184, 0.16)',
  dot: '#93c5fd',
  dotAccent: '#38bdf8',
}

const LIGHT_CROSS_PALETTE: AstrolabePalette = {
  glow0: 'rgba(37, 99, 235, 0.04)',
  glow1: 'rgba(14, 165, 233, 0.01)',
  axis: 'rgba(15, 23, 42, 0.05)',
  ring: 'rgba(15, 23, 42, 0.065)',
  ringAccent: 'rgba(37, 99, 235, 0.15)',
  tickMajor: 'rgba(37, 99, 235, 0.22)',
  tickMinor: 'rgba(15, 23, 42, 0.07)',
  dot: '#60a5fa',
  dotAccent: '#2563eb',
}

const DAYLIGHT_PALETTE: AstrolabePalette = {
  glow0: 'rgba(37, 99, 235, 0.09)',
  glow1: 'rgba(56, 189, 248, 0.03)',
  axis: 'rgba(15, 23, 42, 0.07)',
  ring: 'rgba(15, 23, 42, 0.11)',
  ringAccent: 'rgba(37, 99, 235, 0.34)',
  tickMajor: 'rgba(37, 99, 235, 0.42)',
  tickMinor: 'rgba(15, 23, 42, 0.13)',
  dot: '#3b82f6',
  dotAccent: '#1d4ed8',
}

function drawAstrolabe(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  baseR: number,
  t: number,
  mx: number,
  palette: AstrolabePalette,
): void {
  const grad = ctx.createRadialGradient(cx, cy, 8, cx, cy, baseR * 1.25)
  grad.addColorStop(0, palette.glow0)
  grad.addColorStop(0.55, palette.glow1)
  grad.addColorStop(1, 'rgba(0,0,0,0)')
  ctx.fillStyle = grad
  ctx.beginPath()
  ctx.arc(cx, cy, baseR * 1.3, 0, Math.PI * 2)
  ctx.fill()

  ctx.strokeStyle = palette.axis
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(cx - baseR * 1.35, cy)
  ctx.lineTo(cx + baseR * 1.35, cy)
  ctx.moveTo(cx, cy - baseR * 1.35)
  ctx.lineTo(cx, cy + baseR * 1.35)
  ctx.stroke()

  const ringScales = [0.32, 0.54, 0.78, 1.06, 1.34]
  ringScales.forEach((scale, idx) => {
    const r = baseR * scale
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate((idx % 2 === 0 ? 1 : -1) * t * 0.000075 * (idx + 1) + (mx - 0.5) * (0.2 + idx * 0.09))

    ctx.beginPath()
    ctx.arc(0, 0, r, 0, Math.PI * 2)
    ctx.strokeStyle = idx === 2 ? palette.ringAccent : palette.ring
    ctx.lineWidth = idx === 2 ? 1.25 : 0.95
    if (idx === 1 || idx === 4) ctx.setLineDash([4, 9])
    ctx.stroke()
    ctx.setLineDash([])

    const ticks = idx >= 3 ? 72 : idx === 2 ? 48 : 24
    for (let i = 0; i < ticks; i += 1) {
      const a = (i / ticks) * Math.PI * 2
      const major = i % 6 === 0
      const len = major ? 9 : 4
      ctx.beginPath()
      ctx.moveTo(Math.cos(a) * (r - len), Math.sin(a) * (r - len))
      ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r)
      ctx.strokeStyle = major ? palette.tickMajor : palette.tickMinor
      ctx.lineWidth = major ? 1.15 : 0.75
      ctx.stroke()
    }

    if (idx < 4) {
      const dotA = t * 0.00032 * (idx % 2 === 0 ? 1 : -1.25) + idx * 1.65
      ctx.beginPath()
      ctx.arc(Math.cos(dotA) * r, Math.sin(dotA) * r, idx === 2 ? 3.5 : 2.5, 0, Math.PI * 2)
      ctx.fillStyle = idx === 2 ? palette.dotAccent : palette.dot
      ctx.fill()
    }
    ctx.restore()
  })
}

export interface RenderThemeOptions {
  /** F1 右侧亮色登录区的像素宽度（< 1024px 移动端时为 0） */
  panelWidth?: number
  /** F3 当前选中的能力卫星索引 */
  activeSatellite?: number
}

export function renderLoginThemeFrame(
  ctx: CanvasRenderingContext2D,
  theme: LoginThemeId,
  width: number,
  height: number,
  t: number,
  mx: number,
  my: number,
  opts: RenderThemeOptions = {},
): void {
  if (!width || !height) return

  if (theme === 'f1') {
    const panelWidth = opts.panelWidth ?? 460
    const splitX = width >= 1024 ? Math.max(0, width - panelWidth) : 0
    const cx = splitX * 0.62 + (mx - 0.5) * 26
    const cy = height * 0.52 + (my - 0.5) * 22
    const baseR = Math.min(width, height) * 0.43

    if (splitX > 0) {
      ctx.save()
      ctx.beginPath()
      ctx.rect(0, 0, splitX, height)
      ctx.clip()
      const bgDark = ctx.createLinearGradient(0, 0, splitX, height)
      bgDark.addColorStop(0, '#070c17')
      bgDark.addColorStop(1, '#0b1426')
      ctx.fillStyle = bgDark
      ctx.fillRect(0, 0, splitX, height)
      drawAstrolabe(ctx, cx, cy, baseR, t, mx, DARK_PALETTE)
      ctx.restore()
    }

    ctx.save()
    ctx.beginPath()
    ctx.rect(splitX, 0, width - splitX, height)
    ctx.clip()
    const bgLight = ctx.createLinearGradient(splitX, 0, width, height)
    bgLight.addColorStop(0, '#eef3f9')
    bgLight.addColorStop(1, '#f7f9fc')
    ctx.fillStyle = bgLight
    ctx.fillRect(splitX, 0, width - splitX, height)
    if (splitX > 0) {
      drawAstrolabe(ctx, cx, cy, baseR, t, mx, LIGHT_CROSS_PALETTE)
    }
    ctx.restore()
    return
  }

  if (theme === 'f2') {
    ctx.fillStyle = '#070b14'
    ctx.fillRect(0, 0, width, height)
    drawAstrolabe(
      ctx,
      width * 0.34 + (mx - 0.5) * 30,
      height * 0.52 + (my - 0.5) * 24,
      Math.min(width, height) * 0.42,
      t,
      mx,
      DARK_PALETTE,
    )
    return
  }

  if (theme === 'f3') {
    const activeSat = opts.activeSatellite ?? 0
    ctx.fillStyle = '#080d17'
    ctx.fillRect(0, 0, width, height)
    const cx = width * 0.35 + (mx - 0.5) * 26
    const cy = height * 0.48 + (my - 0.5) * 22
    const baseR = Math.min(width, height) * 0.38

    const g = ctx.createRadialGradient(cx, cy, 4, cx, cy, baseR * 1.3)
    g.addColorStop(0, 'rgba(56, 189, 248, 0.16)')
    g.addColorStop(0.5, 'rgba(37, 99, 235, 0.05)')
    g.addColorStop(1, 'rgba(8, 13, 23, 0)')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, width, height)

    ctx.beginPath()
    ctx.arc(cx, cy, 18, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(125, 211, 252, 0.45)'
    ctx.lineWidth = 1.2
    ctx.stroke()

    ctx.beginPath()
    ctx.arc(cx, cy, 4, 0, Math.PI * 2)
    ctx.fillStyle = '#38bdf8'
    ctx.fill()

    F3_SATELLITES.forEach((sat, idx) => {
      const r = baseR * sat.orbit
      const isSel = idx === activeSat
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, Math.PI * 2)
      ctx.strokeStyle = isSel ? 'rgba(56, 189, 248, 0.42)' : 'rgba(148, 163, 184, 0.14)'
      ctx.lineWidth = isSel ? 1.4 : 0.9
      if (!isSel && idx % 2 === 1) ctx.setLineDash([4, 8])
      ctx.stroke()
      ctx.setLineDash([])

      const a = t * sat.speed + sat.phase + (mx - 0.5) * 0.35
      const sx = cx + Math.cos(a) * r
      const sy = cy + Math.sin(a) * r

      if (isSel) {
        ctx.beginPath()
        ctx.moveTo(cx, cy)
        ctx.lineTo(sx, sy)
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.32)'
        ctx.lineWidth = 1
        ctx.setLineDash([3, 4])
        ctx.stroke()
        ctx.setLineDash([])

        ctx.beginPath()
        ctx.arc(sx, sy, 13, 0, Math.PI * 2)
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.72)'
        ctx.lineWidth = 1.2
        ctx.stroke()
      }

      ctx.beginPath()
      ctx.arc(sx, sy, isSel ? 5 : 3.5, 0, Math.PI * 2)
      ctx.fillStyle = isSel ? '#38bdf8' : '#94a3b8'
      ctx.fill()

      ctx.font = '600 10px "SF Mono", ui-monospace, monospace'
      ctx.fillStyle = isSel ? '#e0f2fe' : 'rgba(148, 163, 184, 0.65)'
      ctx.fillText(sat.name, sx + (isSel ? 18 : 10), sy + 3)
    })
    return
  }

  if (theme === 'f4') {
    const bg = ctx.createLinearGradient(0, 0, width, height)
    bg.addColorStop(0, '#edf2f8')
    bg.addColorStop(1, '#e6ecf5')
    ctx.fillStyle = bg
    ctx.fillRect(0, 0, width, height)
    drawAstrolabe(
      ctx,
      width * 0.36 + (mx - 0.5) * 28,
      height * 0.52 + (my - 0.5) * 22,
      Math.min(width, height) * 0.43,
      t,
      mx,
      DAYLIGHT_PALETTE,
    )
    return
  }

  if (theme === 'g') {
    const bg = ctx.createLinearGradient(0, 0, width, height)
    bg.addColorStop(0, '#f7f5f0')
    bg.addColorStop(1, '#edeae2')
    ctx.fillStyle = bg
    ctx.fillRect(0, 0, width, height)

    const sunX = width * 0.28 + (mx - 0.5) * 60
    const sunY = height * 0.24 + (my - 0.5) * 40
    const sun = ctx.createRadialGradient(sunX, sunY, 10, sunX, sunY, 320)
    sun.addColorStop(0, 'rgba(217, 179, 130, 0.22)')
    sun.addColorStop(0.6, 'rgba(217, 179, 130, 0.06)')
    sun.addColorStop(1, 'rgba(247, 245, 240, 0)')
    ctx.fillStyle = sun
    ctx.fillRect(0, 0, width, height)

    const layers = [
      { base: 0.58, amp: 56, freq: 2.2, speed: 0.00018, fill: 'rgba(54, 65, 82, 0.055)', stroke: 'rgba(54, 65, 82, 0.14)' },
      { base: 0.64, amp: 48, freq: 2.8, speed: -0.00022, fill: 'rgba(43, 53, 68, 0.07)', stroke: 'rgba(43, 53, 68, 0.18)' },
      { base: 0.70, amp: 44, freq: 3.2, speed: 0.00015, fill: 'rgba(34, 42, 54, 0.085)', stroke: 'rgba(34, 42, 54, 0.20)' },
      { base: 0.77, amp: 36, freq: 3.6, speed: -0.00019, fill: 'rgba(26, 32, 42, 0.10)', stroke: 'rgba(26, 32, 42, 0.22)' },
      { base: 0.85, amp: 28, freq: 4.1, speed: 0.00012, fill: 'rgba(22, 26, 34, 0.12)', stroke: 'rgba(22, 26, 34, 0.26)' },
    ]
    layers.forEach((ly, idx) => {
      ctx.beginPath()
      ctx.moveTo(0, height)
      for (let i = 0; i <= 80; i += 1) {
        const u = i / 80
        const x = u * width
        const parallax = (mx - 0.5) * (idx + 1) * 18
        const ridge =
          Math.sin(u * Math.PI * ly.freq + t * ly.speed + idx * 1.4) * ly.amp +
          Math.cos(u * Math.PI * (ly.freq * 1.8) - t * ly.speed * 0.7 + idx) * (ly.amp * 0.38) +
          Math.exp(-Math.pow((u - mx) * 3.8, 2)) * ((0.5 - my) * 28)
        ctx.lineTo(x, height * ly.base + ridge + parallax * 0.15)
      }
      ctx.lineTo(width, height)
      ctx.closePath()
      ctx.fillStyle = ly.fill
      ctx.fill()
      ctx.strokeStyle = ly.stroke
      ctx.lineWidth = 1
      ctx.stroke()
    })
    return
  }

  if (theme === 'h') {
    ctx.fillStyle = '#f1f4f8'
    ctx.fillRect(0, 0, width, height)

    const s1x = width * 0.28 + Math.cos(t * 0.00045) * 46
    const s1y = height * 0.48 + Math.sin(t * 0.00038) * 38
    const s2x = width * (0.24 + mx * 0.32)
    const s2y = height * (0.22 + my * 0.56)

    const step = 22
    const count = 34
    ctx.lineWidth = 0.9

    for (let i = 1; i <= count; i += 1) {
      const r = i * step + ((t * 0.02) % step)
      const a = Math.max(0.02, 0.22 * (1 - i / count))
      ctx.beginPath()
      ctx.arc(s1x, s1y, r, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(15, 23, 42, ${a.toFixed(3)})`
      ctx.stroke()
    }

    for (let i = 1; i <= count; i += 1) {
      const r = i * step + ((t * 0.02) % step)
      const a = Math.max(0.02, 0.26 * (1 - i / count))
      ctx.beginPath()
      ctx.arc(s2x, s2y, r, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(37, 99, 235, ${a.toFixed(3)})`
      ctx.stroke()
    }

    const centers: Array<[number, number, string]> = [
      [s1x, s1y, '#0f172a'],
      [s2x, s2y, '#2563eb'],
    ]
    centers.forEach(([x, y, col]) => {
      ctx.beginPath()
      ctx.arc(x, y, 4, 0, Math.PI * 2)
      ctx.fillStyle = col
      ctx.fill()
      ctx.beginPath()
      ctx.arc(x, y, 10, 0, Math.PI * 2)
      ctx.strokeStyle = col
      ctx.lineWidth = 1
      ctx.stroke()
    })
    return
  }

  if (theme === 'i') {
    ctx.fillStyle = '#0c1629'
    ctx.fillRect(0, 0, width, height)

    const blobs = [
      { x: 0.18 + Math.sin(t * 0.00035) * 0.08 + (mx - 0.5) * 0.14, y: 0.35, rx: 0.24, ry: 0.72, c: 'rgba(37, 99, 235, 0.78)' },
      { x: 0.42 + Math.cos(t * 0.00028) * 0.10 + (mx - 0.5) * 0.18, y: 0.58, rx: 0.22, ry: 0.68, c: 'rgba(14, 165, 233, 0.72)' },
      { x: 0.60 + Math.sin(t * 0.00022 + 2) * 0.08, y: 0.26, rx: 0.18, ry: 0.58, c: 'rgba(56, 189, 248, 0.52)' },
      { x: 0.28 + Math.cos(t * 0.0002 + 1) * 0.07, y: 0.80, rx: 0.20, ry: 0.45, c: 'rgba(245, 158, 11, 0.32)' },
    ]
    blobs.forEach((b) => {
      const gx = b.x * width
      const gy = b.y * height
      const r = Math.max(width * b.rx, height * b.ry)
      const g = ctx.createRadialGradient(gx, gy, 10, gx, gy, r)
      g.addColorStop(0, b.c)
      g.addColorStop(1, 'rgba(12, 22, 41, 0)')
      ctx.fillStyle = g
      ctx.fillRect(0, 0, width, height)
    })
    return
  }

  // theme === 'j'（航空精密地平仪 HUD）
  ctx.fillStyle = '#0b110f'
  ctx.fillRect(0, 0, width, height)

  const cx = width * 0.34
  const cy = height * 0.5
  const roll = (mx - 0.5) * 0.36
  const pitchPx = (0.5 - my) * 95
  const r = Math.min(width, height) * 0.34

  const glow = ctx.createRadialGradient(cx, cy, 10, cx, cy, r * 1.45)
  glow.addColorStop(0, 'rgba(16, 185, 129, 0.13)')
  glow.addColorStop(0.6, 'rgba(16, 185, 129, 0.03)')
  glow.addColorStop(1, 'rgba(11, 17, 15, 0)')
  ctx.fillStyle = glow
  ctx.fillRect(0, 0, width, height)

  const tapeY = 74
  ctx.strokeStyle = 'rgba(110, 231, 183, 0.22)'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(cx - 210, tapeY)
  ctx.lineTo(cx + 210, tapeY)
  ctx.stroke()

  const shift = (mx - 0.5) * 140
  for (let i = -14; i <= 14; i += 1) {
    const x = cx + i * 16 - (shift % 16)
    if (x < cx - 208 || x > cx + 208) continue
    const major = i % 3 === 0
    ctx.beginPath()
    ctx.moveTo(x, tapeY)
    ctx.lineTo(x, tapeY - (major ? 10 : 5))
    ctx.strokeStyle = major ? 'rgba(52, 211, 153, 0.5)' : 'rgba(110, 231, 183, 0.22)'
    ctx.stroke()
  }

  ctx.save()
  ctx.beginPath()
  ctx.arc(cx, cy, r, 0, Math.PI * 2)
  ctx.strokeStyle = 'rgba(110, 231, 183, 0.22)'
  ctx.lineWidth = 1.1
  ctx.stroke()
  ctx.clip()

  ctx.save()
  ctx.translate(cx, cy + pitchPx)
  ctx.rotate(roll)

  ctx.fillStyle = 'rgba(16, 185, 129, 0.045)'
  ctx.fillRect(-r * 2, 0, r * 4, r * 2)

  ctx.beginPath()
  ctx.moveTo(-r * 1.4, 0)
  ctx.lineTo(r * 1.4, 0)
  ctx.strokeStyle = 'rgba(52, 211, 153, 0.55)'
  ctx.lineWidth = 1.4
  ctx.stroke()

  for (let p = -5; p <= 5; p += 1) {
    if (p === 0) continue
    const py = p * 34
    const w = p % 2 === 0 ? 58 : 30
    ctx.strokeStyle = p < 0 ? 'rgba(110, 231, 183, 0.34)' : 'rgba(245, 158, 11, 0.34)'
    ctx.lineWidth = 1
    if (p > 0) ctx.setLineDash([4, 4])
    ctx.beginPath()
    ctx.moveTo(-w, py)
    ctx.lineTo(-14, py)
    ctx.moveTo(14, py)
    ctx.lineTo(w, py)
    ctx.stroke()
    ctx.setLineDash([])
  }
  ctx.restore()
  ctx.restore()

  ctx.strokeStyle = '#f59e0b'
  ctx.lineWidth = 1.6
  ctx.beginPath()
  ctx.moveTo(cx - 28, cy)
  ctx.lineTo(cx - 8, cy)
  ctx.lineTo(cx - 8, cy + 6)
  ctx.moveTo(cx + 28, cy)
  ctx.lineTo(cx + 8, cy)
  ctx.lineTo(cx + 8, cy + 6)
  ctx.stroke()

  ctx.beginPath()
  ctx.arc(cx, cy, 2.2, 0, Math.PI * 2)
  ctx.fillStyle = '#f59e0b'
  ctx.fill()
}
