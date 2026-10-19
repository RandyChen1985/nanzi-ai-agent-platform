/**
 * 通用 API 工具「参数定义」（``sys_api_tools.parameter_schema``）的解析与序列化。
 *
 * 抽成纯函数的原因：运行时 ``GenericApiToolFactory`` 同时兼容三种写法，还带两个反直觉行为，
 * 一旦解析/序列化出错就是**静默写坏用户数据**。放在这里可以用 Node 直接跑往返测试，
 * 而不是只靠组件源码的静态断言。
 *
 * 运行时事实（详见 ``app/services/ai/tools/generic_api.py``）：
 * 1. 标准 JSON Schema：读 ``properties``，可选读顶层 ``required`` 数组；
 * 2. 扁平 map：顶层即参数表（``type`` / ``required`` / ``$schema`` 会被跳过）；
 * 3. 极简写法：``"param": "描述"``，值当描述；
 * 4. **参数缺省是必填**（``param_def.get("required", True)``），与标准 JSON Schema 的默认可选相反；
 * 5. 标准形态下 ``required: []`` 表达不了「全部可选」——空数组是 falsy，会掉回默认必填分支，
 *    因此非必填参数必须写内联 ``required: false``；
 * 6. ``default`` 只在非必填时被读取；
 * 7. 顶层扩展键（如 ``x-nanzi-evidence-types``）另有含义，不能被编辑器吃掉。
 *
 * 由此确定两条红线：只接管 ``properties`` / ``required``，其余键原样保留；保存时维持原有形态。
 */

export type ToolSchemaShape = 'json-schema' | 'flat'

export const SYSTEM_PARAMS_KEY = 'x-nanzi-system-params'
export const SYSTEM_PARAM_TYPES = ['string', 'integer', 'number', 'boolean'] as const
export type SystemParamType = typeof SYSTEM_PARAM_TYPES[number]

/** 系统级参数：模型不可见，调用前由平台强制注入。 */
export type SystemParam = {
    name: string
    type: SystemParamType
    value: string
    description: string
}

/** 可视化编辑器里的一行参数（与 ``key`` 无关的纯数据部分）。 */
export type SchemaParam = {
    name: string
    type: string
    required: boolean
    description: string
    defaultValue: string
    /** 该参数原有的其他键（items / properties / format / enum …），原样带回。 */
    extra: Record<string, any>
}

export type ParsedToolSchema = {
    shape: ToolSchemaShape
    /**
     * 原本是否带顶层 ``required`` 数组。
     *
     * 决定序列化时要不要写它：原本没有就不凭空添加——运行时缺省语义（默认必填）等价，
     * 但能避免「只是来回切一下编辑模式，JSON 就多出几行」的困扰。
     */
    hadRequiredList: boolean
    /** 除 properties / required 之外的顶层键，原样保留。 */
    rest: Record<string, any>
    params: SchemaParam[]
    /** 顶层 x-nanzi-system-params；不进入 properties。 */
    systemParams: SystemParam[]
}

export const PARAM_TYPE_OPTIONS = ['string', 'number', 'integer', 'boolean', 'array', 'object']

/** 扁平形态下这些顶层键不是参数。 */
export const NON_PARAM_SCHEMA_KEYS = ['type', 'required', '$schema', 'properties', 'description']

export const coerceDefaultValue = (raw: string, type: string): any => {
    if (type === 'number' || type === 'integer') {
        const parsed = Number(raw)
        return Number.isFinite(parsed) ? parsed : raw
    }
    if (type === 'boolean') return raw === 'true'
    return raw
}

const isNonParamKey = (key: string) => NON_PARAM_SCHEMA_KEYS.includes(key) || key.startsWith('x-')

/**
 * 解析参数定义。**非法 JSON 返回 ``null``**，由调用方决定如何提示——绝不静默吞掉。
 */
export const parseToolSchema = (jsonText: string): ParsedToolSchema | null => {
    let parsed: any
    try {
        parsed = JSON.parse(jsonText || '{}')
    } catch (e) {
        return null
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null

    const standard = !!parsed.properties
        && typeof parsed.properties === 'object'
        && !Array.isArray(parsed.properties)
    const shape: ToolSchemaShape = standard ? 'json-schema' : 'flat'

    const rawSystemParams = parsed[SYSTEM_PARAMS_KEY]
    const systemParams: SystemParam[] = Array.isArray(rawSystemParams)
        ? rawSystemParams.map((entry: any) => ({
            name: typeof entry?.name === 'string' ? entry.name : '',
            type: SYSTEM_PARAM_TYPES.includes(entry?.type) ? entry.type : 'string',
            value: typeof entry?.value === 'string' ? entry.value : '',
            description: typeof entry?.description === 'string' ? entry.description : '',
        }))
        : []

    const rest: Record<string, any> = {}
    for (const [key, value] of Object.entries(parsed)) {
        if (key === SYSTEM_PARAMS_KEY && Array.isArray(value)) continue
        if (standard) {
            if (key !== 'properties' && key !== 'required') rest[key] = value
        } else if (isNonParamKey(key)) {
            rest[key] = value
        }
    }

    const paramMap: Record<string, any> = standard ? parsed.properties : parsed
    const requiredList: string[] | null = Array.isArray(parsed.required) ? parsed.required : null
    const params: SchemaParam[] = []
    for (const [name, def] of Object.entries(paramMap)) {
        if (!standard && isNonParamKey(name)) continue
        if (typeof def === 'string') {
            // 极简写法 "param": "描述"
            params.push({ name, type: 'string', required: true, description: def, defaultValue: '', extra: {} })
            continue
        }
        if (!def || typeof def !== 'object' || Array.isArray(def)) {
            params.push({ name, type: 'string', required: true, description: '', defaultValue: '', extra: {} })
            continue
        }
        const { type, description, required, default: defaultValue, ...extra } = def as Record<string, any>
        params.push({
            name,
            type: typeof type === 'string' && type ? type : 'string',
            // 标准形态看顶层 required 数组；缺省（含空数组）回落运行时的「默认必填」语义
            required: requiredList ? requiredList.includes(name) : required !== false,
            description: typeof description === 'string' ? description : '',
            defaultValue: defaultValue === undefined || defaultValue === null ? '' : String(defaultValue),
            extra,
        })
    }

    return { shape, hadRequiredList: requiredList !== null, rest, params, systemParams }
}

/**
 * 序列化回 JSON 文本，**保持 ``schema.shape`` 指示的原有形态**。
 */
export const serializeToolSchema = (schema: ParsedToolSchema): string => {
    const properties: Record<string, any> = {}
    const requiredNames: string[] = []
    const systemNames = new Set(
        schema.systemParams.map((param) => param.name.trim()).filter(Boolean),
    )
    for (const param of schema.params) {
        const name = param.name.trim()
        if (!name) continue
        const def: Record<string, any> = { ...param.extra, type: param.type }
        const description = param.description.trim()
        if (description) def.description = description
        if (param.required) {
            if (!systemNames.has(name)) requiredNames.push(name)
        } else {
            // 运行时缺省必填，只有内联 required: false 才能表达「非必填」
            def.required = false
            const rawDefault = param.defaultValue.trim()
            if (rawDefault) def.default = coerceDefaultValue(rawDefault, param.type)
        }
        properties[name] = def
    }

    const payload: Record<string, any> = { ...schema.rest }
    if (schema.shape === 'json-schema') {
        payload.properties = properties
        // 原本没有顶层 required 数组时不凭空添加：运行时的缺省语义等价（默认必填），
        // 但不会让用户在「什么都没改」的情况下看到 JSON 多出内容
        if (schema.hadRequiredList) payload.required = requiredNames
    } else {
        Object.assign(payload, properties)
    }
    if (schema.systemParams.length > 0) {
        payload[SYSTEM_PARAMS_KEY] = schema.systemParams
    }
    return JSON.stringify(payload, null, 2)
}

/** 校验系统级参数；返回空数组表示通过。 */
export const validateSystemParams = (
    systemParams: SystemParam[],
    params: Array<Pick<SchemaParam, 'name'>>,
): string[] => {
    const errors: string[] = []
    const seen = new Set<string>()
    const normalNames = new Set(params.map((param) => param.name.trim()).filter(Boolean))

    systemParams.forEach((param, index) => {
        const name = param.name.trim()
        if (!name) {
            errors.push(`第 ${index + 1} 个系统级参数缺少名称`)
        } else if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
            errors.push(`系统级参数名不合法：${name}`)
        } else if (seen.has(name)) {
            errors.push(`系统级参数名重复：${name}`)
        } else if (normalNames.has(name)) {
            errors.push(`系统级参数 ${name} 与普通参数重名`)
        }
        seen.add(name)
        if (!SYSTEM_PARAM_TYPES.includes(param.type)) {
            errors.push(`系统级参数 ${name || index + 1} 的类型不支持：${param.type}`)
        }
        if (!param.value.trim()) {
            errors.push(`系统级参数 ${name || index + 1} 缺少固定值`)
        }
    })
    return errors
}
