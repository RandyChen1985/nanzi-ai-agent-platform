export interface TemperatureScaleGuidance {
  range: string
  max: number
  description: string
}

export const temperatureScaleGuidance: TemperatureScaleGuidance[] = [
  {
    range: '0.0～0.3',
    max: 0.3,
    description: '更稳定严谨，适合查数、代码和规则问答。',
  },
  {
    range: '0.4～0.8',
    max: 0.8,
    description: '准确性和表达多样性较均衡，适合日常对话。',
  },
  {
    range: '0.9～1.0',
    max: 1,
    description: '回答更灵活，措辞变化更多。',
  },
]

export const getTemperatureGuidance = (value: unknown): string => {
  const temperature = Number(value)
  if (Number.isFinite(temperature)) {
    const guidance = temperatureScaleGuidance.find((item) => temperature <= item.max)
    if (guidance) return guidance.description
    return '创造性和随机性更强，但可能偏离主题；请先确认模型官方文档支持。'
  }
  return temperatureScaleGuidance[1].description
}
