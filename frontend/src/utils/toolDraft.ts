/**
 * 由「解析出来的请求」生成参数清单：判落位、判必填、推类型。
 *
 * **落位规则必须与运行时 ``GenericApiToolFactory._execute_request`` 同源**，
 * 否则向导会给出「试跑能过、运行时调不通」的假结论：
 *   1. 地址里出现 ``{name}`` → 路径替换（**与方法无关**，运行时最先做这一步）；
 *   2. 剩下的参数：GET 走 query，POST/PUT/PATCH/DELETE 走 JSON body。
 *
 * **必填规则**（对齐运行时的「缺省必填」语义）：
 *   * 值为 ``{city}`` 占位符 → 必填，无默认值（无论它在 path 还是 query 里）；
 *   * 请求里带了**非空且非占位符**的值 → 可选 + 默认值 = 该值（让使用者少填就少错）；
 *   * 其余（``?city=`` 这种空值）→ 必填。
 *
 * 序列化一律复用 ``toolSchema.ts``：新建时用**扁平形态**（与表单 placeholder 同款），
 * 非必填由它落成内联 ``required: false``——标准形态下的 ``required: []`` 表达不了
 * 「全部可选」（空数组是 falsy，运行时掉回默认必填分支）。
 */
import type { ParsedRequest } from './toolImport.ts'
import { serializeToolSchema, type SystemParam } from './toolSchema.ts'

export type ParamLocation = 'path' | 'query' | 'body'

export type DraftParam = {
    name: string
    type: string
    required: boolean
    description: string
    defaultValue: string
    location: ParamLocation
    /** 给用户看的「为什么这么判」，第 3 步逐行展示（界面不自己拼文案） */
    reason: string
}

const isPlaceholderValue = (value: string): boolean => /^\{[^{}]+\}$/.test(value)

/** 判定单个参数在请求里的落位（与运行时同源）。 */
export const resolveParamLocation = (
    method: string,
    urlTemplate: string,
    name: string,
): ParamLocation => {
    if (!name) return 'query'
    if (urlTemplate.includes(`{${name}}`)) return 'path'
    return String(method || 'GET').trim().toUpperCase() === 'GET' ? 'query' : 'body'
}

const locationLabel = (location: ParamLocation): string =>
    location === 'path' ? '路径参数' : location === 'query' ? '查询参数' : '请求体'

/** 由解析结果生成参数清单（顺序保持原请求里的出现次序，同名只保留第一次）。 */
export const buildDraftParams = (parsed: ParsedRequest): DraftParam[] => {
    const method = String(parsed.method || 'GET').trim().toUpperCase()
    const urlTemplate = parsed.urlTemplate || ''
    const params: DraftParam[] = []
    const seen = new Set<string>()

    const push = (name: string, rawValue: string, type: string, location: ParamLocation) => {
        const trimmed = String(name || '').trim()
        if (!trimmed || seen.has(trimmed)) return
        seen.add(trimmed)
        const required = rawValue === '' || isPlaceholderValue(rawValue)
        params.push({
            name: trimmed,
            type: type || 'string',
            required,
            description: '',
            defaultValue: required ? '' : rawValue,
            location,
            reason: required
                ? `${locationLabel(location)}${isPlaceholderValue(rawValue)
                    ? '，原始请求里是 { } 占位符，需要填值'
                    : '，原始请求里没有给值'} → 判为必填`
                : `${locationLabel(location)}，原始请求里带了值 ${rawValue} → 已设为可选并作为默认值`,
        })
    }

    // 1. 地址路径里的占位符：运行时对它们做路径替换
    for (const name of parsed.pathParams || []) {
        push(name, `{${name}}`, 'string', 'path')
    }

    // 2. 其余 query 参数。**不要**因为值是 {b} 就跳过——那不是路径替换，
    //    只是一个需要模型填值的查询参数（跳掉会把参数整个丢掉）。
    for (const item of parsed.queryParams || []) {
        push(item.name, item.value, 'string', resolveParamLocation(method, urlTemplate, item.name))
    }

    // 3. 请求体参数
    for (const item of parsed.bodyParams || []) {
        push(item.name, item.value, item.type || 'string', 'body')
    }

    return params
}

/** 参数清单 → ``parameter_schema`` 文本（扁平形态，非必填落成内联 required:false）。 */
export const buildDraftSchema = (
    params: DraftParam[],
    systemParams: SystemParam[] = [],
): string => serializeToolSchema({
    shape: 'flat',
    hadRequiredList: false,
    rest: {},
    params: params.map((param) => ({
        name: param.name,
        type: param.type,
        required: param.required,
        description: param.description,
        defaultValue: param.defaultValue,
        extra: {},
    })),
    systemParams,
})

/** 参数清单 → 落位映射，供 ``ToolParamEditor`` 在向导模式里显示标签。 */
export const buildLocationMap = (params: DraftParam[]): Record<string, ParamLocation> => {
    const map: Record<string, ParamLocation> = {}
    for (const param of params) {
        if (param.name) map[param.name] = param.location
    }
    return map
}
