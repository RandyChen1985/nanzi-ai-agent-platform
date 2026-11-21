/**
 * 把「接口文档里的一条请求」解析成结构化数据——支持完整 URL 与 curl 命令。
 *
 * 刻意只做前端纯函数：可以用 ``node --experimental-strip-types`` 直接跑往返测试
 * （见 ``tests/frontend/tool_import_roundtrip.mjs``），不碰 DOM、不碰组件状态、
 * 不新增后端端点。
 *
 * curl 只取 ``-X`` / ``--request`` / ``-H`` / ``--header`` / ``-d`` / ``--data-*`` /
 * ``--url``，**不做完整 shell 语法解析**：认不出的开关降级为 warning 并保留原文，绝不猜。
 *
 * 一个容易搞错的语义（第一版曾写错）：``pathParams`` **只装 URL 路径里**的
 * ``{param}``。query 里的 ``?b={b}`` 不是路径替换——运行时的 ``url_template``
 * 已被剥掉 query，替换只作用于路径；``b`` 就是一个普通的查询参数，只是它的值
 * 需要模型来填。
 */
// 注意：这里的 ``.ts`` 扩展名不能省。本模块会被 ``node --experimental-strip-types``
// 直接执行（tests/frontend/tool_import_roundtrip.mjs），Node 的 ESM 解析不做
// 扩展名补全。仓库里被 Node 直跑的纯函数模块都是这个写法
// （见 src/utils/chatSessionExport.ts、generatedFilePreviewPlan.ts）。
import { isSupportedMethod, SUPPORTED_HTTP_METHODS } from './toolMethods.ts'

export type ParsedQueryParam = { name: string; value: string }
export type ParsedBodyParam = { name: string; value: string; type: string }

export type ParsedRequest = {
    method: string
    /** 去掉 query 后的地址模板，保留路径里的 ``{param}`` 占位符 */
    urlTemplate: string
    /** **仅** URL 路径里出现的 ``{param}`` 参数名 */
    pathParams: string[]
    queryParams: ParsedQueryParam[]
    bodyParams: ParsedBodyParam[]
    headers: Record<string, string>
    warnings: string[]
}

export type ParseFailure = { error: string }

const URL_RE = /^([a-zA-Z][a-zA-Z0-9+.\-]*:\/\/[^/?#]+)([^?#]*)(?:\?([^#]*))?(?:#.*)?$/
const PLACEHOLDER_RE = /\{([^{}/]+)\}/g
const BODY_OPTION_FLAGS = ['-d', '--data', '--data-raw', '--data-binary', '--data-ascii']

const decodeSafely = (raw: string): string => {
    try {
        return decodeURIComponent(raw)
    } catch {
        // 非法百分号编码（``100%zz``）不该让整个解析失败
        return raw
    }
}

/** 极简 shell 分词：支持单/双引号、反斜杠续行与转义。 */
export const tokenizeShellLike = (text: string): string[] => {
    const tokens: string[] = []
    let current = ''
    let quote: '"' | "'" | null = null
    let touched = false

    for (let i = 0; i < text.length; i += 1) {
        const char = text[i]
        // `text[i]` 在 noUncheckedIndexedAccess 下是 string | undefined；
        // 循环条件已保证下标在界内，这里收口一次，后面就不用处处断言。
        if (char === undefined) break
        if (quote) {
            if (char === quote) {
                quote = null
                continue
            }
            if (char === '\\' && quote === '"') {
                const next = text[i + 1]
                if (next === '"' || next === '\\') {
                    current += next
                    i += 1
                    continue
                }
            }
            current += char
            continue
        }
        if (char === '"' || char === "'") {
            quote = char
            touched = true
            continue
        }
        if (char === '\\') {
            const next = text[i + 1]
            if (next === '\n') { i += 1; continue }            // 续行
            if (next === '\r' && text[i + 2] === '\n') { i += 2; continue }
            if (next === undefined) continue
            current += next
            i += 1
            touched = true
            continue
        }
        if (/\s/.test(char)) {
            if (touched || current) {
                tokens.push(current)
                current = ''
                touched = false
            }
            continue
        }
        current += char
        touched = true
    }
    if (touched || current) tokens.push(current)
    return tokens
}

const inferBodyType = (value: unknown): string => {
    if (typeof value === 'number') return Number.isInteger(value) ? 'integer' : 'number'
    if (typeof value === 'boolean') return 'boolean'
    if (Array.isArray(value)) return 'array'
    if (value && typeof value === 'object') return 'object'
    return 'string'
}

const stringifyBodyValue = (value: unknown): string => {
    if (value === null || value === undefined) return ''
    if (typeof value === 'string') return value
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
}

/** 解析 URL（可含路径 ``{param}`` 占位符）。 */
const parseUrl = (
    raw: string,
    warnings: string[],
): { urlTemplate: string; pathParams: string[]; queryParams: ParsedQueryParam[] } | ParseFailure => {
    let text = raw.trim()
    if (!text) return { error: '没有可解析的内容' }

    let match = text.match(URL_RE)
    if (!match && /^[^\s/]+\.[^\s/]+(\/|$|\?)/.test(text)) {
        // 像域名但没写 scheme：补 https:// 并明确告知（不静默改）
        text = `https://${text}`
        warnings.push('地址没写协议，已按 https:// 处理——如果该接口只支持 http，请手动改回')
        match = text.match(URL_RE)
    }
    if (!match) {
        return { error: '没认出这是一个网址。请粘贴形如 https://api.example.com/v1/xxx 的完整地址' }
    }

    const [, origin, path, queryString] = match
    const urlTemplate = `${origin}${path || ''}`

    const pathParams: string[] = []
    for (const found of `${path || ''}`.matchAll(PLACEHOLDER_RE)) {
        const placeholder = found[1]
        if (placeholder && !pathParams.includes(placeholder)) pathParams.push(placeholder)
    }

    const queryParams: ParsedQueryParam[] = []
    if (queryString) {
        for (const pair of queryString.split('&')) {
            if (!pair) continue
            const index = pair.indexOf('=')
            if (index === -1) {
                queryParams.push({ name: decodeSafely(pair), value: '' })
                continue
            }
            queryParams.push({
                name: decodeSafely(pair.slice(0, index)),
                value: decodeSafely(pair.slice(index + 1)),
            })
        }
    }

    return { urlTemplate, pathParams, queryParams }
}

/** 按方法补两类与方法相关的提示（与运行时行为对齐，避免「配了却调不通」）。 */
const withMethodWarnings = (parsed: ParsedRequest): ParsedRequest => {
    const warnings = [...parsed.warnings]
    if (!isSupportedMethod(parsed.method)) {
        warnings.push(
            `平台运行时不支持 ${parsed.method} 方法，可用的只有 ${SUPPORTED_HTTP_METHODS.join(' / ')}`,
        )
    }
    if (parsed.method !== 'GET' && parsed.queryParams.length > 0) {
        // url_template 已被剥掉 query，运行时非 GET 时把其余参数一律放进 JSON body
        warnings.push(
            '这是非 GET 请求：地址里的查询参数在平台里会作为请求体字段发送；'
            + '如果它们本该是固定的查询条件，请把值直接写进 URL 模板',
        )
    }
    return { ...parsed, warnings }
}

/** 解析 curl 命令。 */
const parseCurl = (text: string): ParsedRequest | ParseFailure => {
    const warnings: string[] = []
    const tokens = tokenizeShellLike(text)
    const rest = tokens[0] && tokens[0].toLowerCase().startsWith('curl') ? tokens.slice(1) : tokens

    let method = ''
    let url = ''
    const headers: Record<string, string> = {}
    let bodyText: string | null = null

    for (let i = 0; i < rest.length; i += 1) {
        const token = rest[i]
        // 同 text[i]：循环条件保证在界内，收口一次免得后面三处各写断言
        if (token === undefined) continue
        const next = rest[i + 1]

        if (token === '-X' || token === '--request') {
            if (next) { method = next.toUpperCase(); i += 1 }
            continue
        }
        if (token === '-H' || token === '--header') {
            if (next !== undefined) {
                const index = next.indexOf(':')
                if (index > 0) {
                    const name = next.slice(0, index).trim()
                    const value = next.slice(index + 1).trim()
                    if (name) headers[name] = value
                } else {
                    warnings.push(`没认出这个请求头：${next}`)
                }
                i += 1
            }
            continue
        }
        if (BODY_OPTION_FLAGS.includes(token)) {
            if (next !== undefined) { bodyText = next; i += 1 }
            continue
        }
        if (token === '--url') {
            if (next !== undefined) { url = next; i += 1 }
            continue
        }
        if (token.startsWith('-')) {
            // 其他开关（-G / --compressed / -u …）本次不支持，明确告知而不是默默忽略
            warnings.push(`暂不支持的 curl 选项已忽略：${token}`)
            continue
        }
        if (!url) url = token
    }

    if (!url) return { error: '这条 curl 里没找到请求地址。请确认命令里带了完整的 URL' }

    const parsedUrl = parseUrl(url, warnings)
    if ('error' in parsedUrl) return parsedUrl

    const bodyParams: ParsedBodyParam[] = []
    if (bodyText !== null && bodyText !== '') {
        let parsedBody: unknown
        try {
            parsedBody = JSON.parse(bodyText)
        } catch {
            warnings.push('请求体不是 JSON，没能自动识别出参数——请在第 3 步手动补上')
            parsedBody = undefined
        }
        if (parsedBody && typeof parsedBody === 'object' && !Array.isArray(parsedBody)) {
            for (const [name, value] of Object.entries(parsedBody as Record<string, unknown>)) {
                bodyParams.push({ name, value: stringifyBodyValue(value), type: inferBodyType(value) })
            }
        } else if (parsedBody !== undefined) {
            warnings.push('请求体不是 JSON 对象，没能自动识别出参数——请在第 3 步手动补上')
        }
    }

    return withMethodWarnings({
        method: method || (bodyParams.length > 0 ? 'POST' : 'GET'),
        urlTemplate: parsedUrl.urlTemplate,
        pathParams: parsedUrl.pathParams,
        queryParams: parsedUrl.queryParams,
        bodyParams,
        headers,
        warnings,
    })
}

/**
 * 解析用户粘贴的内容：curl 命令或一条完整 URL。
 * **任何情况下都不抛异常**——解析不了就返回 ``{ error }`` 由界面原地提示。
 */
export const parseRequestInput = (text: string): ParsedRequest | ParseFailure => {
    const raw = String(text || '').trim()
    if (!raw) return { error: '请先粘贴一条请求地址或 curl 命令' }

    if (/^\s*curl(\s|$)/i.test(raw)) return parseCurl(raw)

    const warnings: string[] = []
    const parsedUrl = parseUrl(raw, warnings)
    if ('error' in parsedUrl) return parsedUrl

    if (parsedUrl.queryParams.length === 0 && parsedUrl.pathParams.length === 0) {
        warnings.push('这条地址里没有任何参数，配出来的工具将不需要输入')
    }

    return withMethodWarnings({
        method: 'GET',
        urlTemplate: parsedUrl.urlTemplate,
        pathParams: parsedUrl.pathParams,
        queryParams: parsedUrl.queryParams,
        bodyParams: [],
        headers: {},
        warnings,
    })
}

export const isParsedRequest = (value: ParsedRequest | ParseFailure): value is ParsedRequest =>
    !('error' in value)
