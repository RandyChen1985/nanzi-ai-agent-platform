export interface TemperatureReference {
  provider: string
  providerKeys: string[]
  range: string
  recommendation: string
  scenarios: string
  officialUrl: string
}

export const temperatureReference: TemperatureReference[] = [
  {
    provider: 'OpenAI',
    providerKeys: ['openai', 'azure'],
    range: '0～2',
    recommendation: '官方示例：0.2 更聚焦，0.8 更随机；没有统一推荐值。',
    scenarios: '事实问答、代码可从 0.2 起；创意场景可从 0.8 起。',
    officialUrl: 'https://developers.openai.com/api/reference/cli/resources/responses/methods/create',
  },
  {
    provider: 'DeepSeek',
    providerKeys: ['deepseek'],
    range: '0～2；思考模式不生效',
    recommendation: '默认 1.0；官方按场景推荐 0.0～1.5。',
    scenarios: '代码/数学 0；数据分析 1.0；普通对话/翻译 1.3；创作 1.5。',
    officialUrl: 'https://api-docs.deepseek.com/quick_start/parameter_settings/',
  },
  {
    provider: 'GLM（智谱）',
    providerKeys: ['zhipu'],
    range: '0～1',
    recommendation: '不同系列默认值约为 0.6～1.0；官方示例为 0.2 或 0.8。',
    scenarios: '事实问答可用 0.2；创意写作或头脑风暴可用 0.8。',
    officialUrl: 'https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8',
  },
  {
    provider: 'Kimi（月之暗面）',
    providerKeys: ['kimi'],
    range: '按模型固定',
    recommendation: 'K2.6：非思考 0.6、思考 1.0；K2.7 Code 和 K3 固定 1.0。',
    scenarios: '部分模型不建议手动传入温度，具体以模型官方文档为准。',
    officialUrl: 'https://platform.kimi.com/docs/api/models-overview',
  },
  {
    provider: 'Qwen（通义千问）',
    providerKeys: ['dashscope'],
    range: '0～2（不含 2）',
    recommendation: '官方示例：0.1 更确定，0.9 更有创造性。',
    scenarios: '事实问答 0.1；代码 0.2；翻译 0.3；创意写作 0.9。',
    officialUrl: 'https://www.alibabacloud.com/help/en/model-studio/completions',
  },
]

