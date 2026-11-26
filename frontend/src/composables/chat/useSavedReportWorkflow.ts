export interface SavedReportRunTarget {
  params_schema?: Array<{
    type?: string;
    name?: string;
    label?: string;
    default?: unknown;
    required?: boolean;
    options?: unknown[];
  }>;
}

export interface SavedReportRunFormValue {
  dateRange: string;
  startDate: string;
  endDate: string;
  monthRange: string;
  startMonth: string;
  endMonth: string;
  customParams: Record<string, any>;
}

export const detectSavedReportDateTemplate = (sql: string) => {
  const matches = [...String(sql || "").matchAll(/'(\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}:\d{2})?'/g)];
  if (matches.length >= 2) {
    const first = matches[0];
    const second = matches[1];
    if (!first || !second || first.index === undefined || second.index === undefined) return null;
    const firstRaw = first[0];
    const secondRaw = second[0];
    const startParam = /\d{2}:\d{2}:\d{2}/.test(firstRaw) ? "start_datetime" : "start_date";
    const endParam = /\d{2}:\d{2}:\d{2}/.test(secondRaw) ? "end_datetime" : "end_date";
    return {
      sql_template: `${sql.slice(0, first.index)}{{${startParam}}}${sql.slice(first.index + firstRaw.length, second.index)}{{${endParam}}}${sql.slice(second.index + secondRaw.length)}`,
      params_schema: [{ name: "date_range", type: "date_range", label: "日期范围", default: "month_start_to_today", options: ["today", "yesterday", "last_7_days", "month_start_to_today", "year_start_to_today", "custom_range"] }],
      default_params: { date_range: "month_start_to_today" },
    };
  }
  const monthMatches = [...String(sql || "").matchAll(/'(\d{4}-\d{2})'/g)];
  if (monthMatches.length < 2) return null;
  const first = monthMatches[0];
  const second = monthMatches[1];
  if (!first || !second || first.index === undefined || second.index === undefined) return null;
  const firstRaw = first[0];
  const secondRaw = second[0];
  return {
    sql_template: `${sql.slice(0, first.index)}{{start_month}}${sql.slice(first.index + firstRaw.length, second.index)}{{end_month}}${sql.slice(second.index + secondRaw.length)}`,
    params_schema: [{ name: "month_range", type: "month_range", label: "月份范围", default: "last_6_completed_months", options: ["last_6_completed_months", "year_start_to_current_month", "custom_month_range"] }],
    default_params: { month_range: "last_6_completed_months" },
  };
};

export const todayDateString = (now = new Date()) =>
  `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

export const todayMonthString = (now = new Date()) =>
  `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

export const parseSavedReportTags = (input: string) => {
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const raw of String(input || "").split(/[,\s，]+/)) {
    const tag = raw.trim();
    if (!tag || seen.has(tag)) continue;
    seen.add(tag);
    tags.push(tag.slice(0, 32));
    if (tags.length >= 12) break;
  }
  return tags;
};

export const renderSavedReportDataToMarkdown = (data: any): string => {
  if (!data) return "执行结果为空";
  const labels: Record<string, string> =
    data?.column_labels && typeof data.column_labels === "object" && !Array.isArray(data.column_labels)
      ? data.column_labels
      : {};
  const labelOf = (name: string) => {
    const raw = String(name || "");
    return String(labels[raw] || labels[raw.toLowerCase()] || raw);
  };
  let columns: string[] = Array.isArray(data.columns)
    ? data.columns.map((column: any) => typeof column === "object" ? (column.name || "") : String(column))
    : [];
  let rows: any[] = [];
  if (Array.isArray(data.rows)) rows = data.rows;
  else if (Array.isArray(data.items)) rows = data.items;
  else if (Array.isArray(data)) rows = data;
  else if (typeof data === "object") rows = Array.isArray(data.data) ? data.data : [data];
  if (!rows.length) return "查询执行成功，但没有返回任何明细数据。";
  if (!columns.length) {
    const firstRow = rows[0];
    if (firstRow && typeof firstRow === "object" && !Array.isArray(firstRow)) columns = Object.keys(firstRow);
    else if (Array.isArray(firstRow)) columns = firstRow.map((_, index) => `列 ${index + 1}`);
    else columns = ["结果值"];
  }
  const displayColumns = columns.map((column) => labelOf(column));
  const maxDisplayRows = 150;
  const displayRows = rows.slice(0, maxDisplayRows);
  let markdown = `\n\n| ${displayColumns.join(" | ")} |\n| ${displayColumns.map(() => "---").join(" | ")} |\n`;
  for (const row of displayRows) {
    let cells: string[] = [];
    if (Array.isArray(row)) {
      cells = row.map((value) => typeof value === "object" ? JSON.stringify(value) : String(value));
    } else if (row && typeof row === "object") {
      cells = columns.map((column) => {
        const value = row[column];
        if (value === null || value === undefined) return "";
        return typeof value === "object" ? JSON.stringify(value) : String(value);
      });
    } else {
      cells = [String(row)];
    }
    markdown += `| ${cells.map((cell) => cell.replace(/\|/g, "\\|").replace(/\n/g, " ")).join(" | ")} |\n`;
  }
  if (rows.length > maxDisplayRows) markdown += `\n> *结果集数据量较大，已在聊天框中自动为您省略后半部分（共展示前 ${maxDisplayRows} 行 / 总计 ${rows.length} 行）。*`;
  return markdown;
};

const looksChinese = (text: string) => /[\u4e00-\u9fff]/.test(text);

const heuristicTermForColumn = (name: string): string | null => {
  const raw = String(name || "").trim();
  if (!raw) return null;
  if (looksChinese(raw)) return raw;
  const tokenMap: Record<string, string> = {
    register: "注册",
    registration: "注册",
    user: "用户",
    users: "用户",
    cust: "客户",
    customer: "客户",
    count: "数量",
    cnt: "数量",
    num: "数量",
    amt: "金额",
    amount: "金额",
    date: "日期",
    time: "时间",
    month: "月份",
    year: "年份",
    create: "创建",
    created: "创建",
    stat: "统计",
  };
  const tokens = raw.replace(/^(t_|dim_|fact_)/i, "").split(/[_\s]+/).filter(Boolean);
  const parts: string[] = [];
  for (const token of tokens) {
    const mapped = tokenMap[token.toLowerCase()];
    if (!mapped) return null;
    if (!parts.includes(mapped)) parts.push(mapped);
  }
  const term = parts.join("");
  return looksChinese(term) ? term : null;
};

/** 从 ChatBI 成功查数消息提取 column_meta，供保存固化报表时固化语义 */
export const extractColumnMetaFromAgentMessage = (msg: any): Record<string, any> | null => {
  const logs = Array.isArray(msg?.logs) ? msg.logs : [];
  for (let i = logs.length - 1; i >= 0; i -= 1) {
    const log = logs[i];
    const label = `${log?.name || ""} ${log?.title || ""}`;
    if (!/execute_sql_query/i.test(label) || log?.status !== "success") continue;
    const details = String(log?.details || "");
    const delim = "--- 结果 ---";
    const idx = details.indexOf(delim);
    if (idx < 0) continue;
    const body = details.slice(idx + delim.length).replace(/\n\n\[系统检测\][\s\S]*$/, "").trim();
    try {
      const parsed = JSON.parse(body);
      const labels: Record<string, string> = {};
      const columns: Array<Record<string, string>> = [];
      const rawColumns = Array.isArray(parsed?.columns) ? parsed.columns : [];
      if (rawColumns.length) {
        for (const col of rawColumns) {
          if (typeof col === "string") {
            const term = heuristicTermForColumn(col);
            columns.push(term ? { name: col, term } : { name: col });
            continue;
          }
          if (!col || typeof col !== "object") continue;
          const name = String(col.name || "").trim();
          if (!name) continue;
          let term = "";
          for (const key of ["term", "label", "display_name", "title", "comment"]) {
            const value = String(col[key] || "").trim();
            if (value && (looksChinese(value) || key === "term")) {
              term = value;
              break;
            }
          }
          if (!term) term = heuristicTermForColumn(name) || "";
          columns.push(term ? { name, term } : { name });
          if (term) labels[name] = term;
        }
      } else {
        let rows: any[] = [];
        if (Array.isArray(parsed?.rows)) rows = parsed.rows;
        else if (Array.isArray(parsed)) rows = parsed;
        const first = rows[0];
        if (first && typeof first === "object" && !Array.isArray(first)) {
          for (const name of Object.keys(first)) {
            const term = heuristicTermForColumn(name);
            columns.push(term ? { name, term } : { name });
            if (term) labels[name] = term;
          }
        }
      }

      // 若助手正文 markdown 表头是中文，尽量对齐物理列顺序
      const content = String(msg?.content || "");
      const tableMatch = content.match(/\n\|([^\n]+)\|\n\|(?:\s*:?-{3,}:?\s*\|)+\n/);
      if (tableMatch && columns.length) {
        const headers = (tableMatch[1] || "")
          .split("|")
          .map((item) => item.trim())
          .filter(Boolean);
        if (headers.length === columns.length) {
          headers.forEach((header, index) => {
            if (!looksChinese(header)) return;
            const col = columns[index];
            if (!col) return;
            if (!col.term || !looksChinese(col.term)) col.term = header;
            const columnName = col.name;
            if (columnName) labels[columnName] = header;
          });
        }
      }

      if (!columns.length) return null;
      return {
        version: 1,
        source: "save_time",
        columns,
      };
    } catch {
      continue;
    }
  }
  return null;
};

export const composeSavedReportExecuteMarkdown = (
  reportTitle: string,
  execResult: any,
): string => {
  const resultMarkdown = renderSavedReportDataToMarkdown(execResult);
  const analysisMarkdown = String(execResult?.analysis_markdown || "").trim();
  const analysisStatus = String(execResult?.analysis_status || "");
  const rowCount = [execResult?.row_count, execResult?.total, execResult?.count]
    .find((value) => typeof value === "number");
  const resultRows = [execResult?.rows, execResult?.items, execResult?.data, execResult?.records]
    .find((value) => Array.isArray(value));
  const rowLabel = rowCount !== undefined ? ` · 返回 ${rowCount} 行` : Array.isArray(resultRows) ? ` · 返回 ${resultRows.length} 行` : "";
  const parts = [
    `### 固化报表「${reportTitle}」执行结果：`,
    `> ✓ 查询成功${rowLabel}`,
    resultMarkdown,
  ];
  if (analysisMarkdown) {
    parts.push("---", "### 📌 业务解读", analysisMarkdown);
  } else if (analysisStatus === "deferred" || analysisStatus === "pending") {
    parts.push("---", "### 📌 业务解读", "> 正在生成业务解读…");
  } else if (analysisStatus === "disabled") {
    parts.push("---", "### 📌 业务解读", "> 未启用业务解读。");
  } else if (analysisStatus && analysisStatus !== "disabled" && analysisStatus !== "success") {
    parts.push("---", "### 📌 业务解读", "> 业务解读暂不可用，可重试解读。");
  }
  return parts.join("\n\n");
};

export const mergeSavedReportAnalysisIntoResult = (execResult: any, analysisResult: any) => ({
  ...(execResult && typeof execResult === "object" ? execResult : {}),
  ...(analysisResult && typeof analysisResult === "object" ? analysisResult : {}),
  column_labels: {
    ...(execResult?.column_labels || {}),
    ...(analysisResult?.column_labels || {}),
  },
  analysis: analysisResult?.analysis ?? execResult?.analysis ?? null,
  analysis_markdown: analysisResult?.analysis_markdown ?? execResult?.analysis_markdown ?? null,
  analysis_status: analysisResult?.analysis_status || execResult?.analysis_status || "fallback",
});

export const buildSavedReportRunParams = (
  report: SavedReportRunTarget | null | undefined,
  form: SavedReportRunFormValue,
) => {
  const usesMonthRange = Boolean(report?.params_schema?.some((item) => item?.type === "month_range" || item?.name === "month_range"));
  const usesDateRange = Boolean(report?.params_schema?.some((item) => item?.type === "date_range" || item?.name === "date_range"));
  const params: Record<string, any> = {};
  if (usesMonthRange) {
    params.month_range = form.monthRange;
    if (form.monthRange === "custom_month_range") {
      params.start_month = form.startMonth;
      params.end_month = form.endMonth;
    }
  } else if (usesDateRange) {
    params.date_range = form.dateRange;
    if (form.dateRange === "custom_range") {
      params.start_date = form.startDate;
      params.end_date = form.endDate;
    }
  }
  for (const item of report?.params_schema || []) {
    const type = String(item?.type || "");
    const name = String(item?.name || "");
    if (!name || type === "date_range" || type === "month_range" || name === "date_range" || name === "month_range") continue;
    if (Object.prototype.hasOwnProperty.call(form.customParams || {}, name)) {
      params[name] = form.customParams[name];
    }
  }
  return params;
};

export const extractSavedReportExecuteErrorMessage = (error: any) => {
  const statusCode = error?.response?.status;
  const responseData = error?.response?.data || {};
  const rawDetail = responseData?.detail ?? responseData?.message ?? responseData?.error;
  const rawMessage = typeof rawDetail === "object" ? JSON.stringify(rawDetail) : String(rawDetail || "");
  const combined = `${rawMessage} ${error?.message || ""}`;
  const lower = combined.toLowerCase();
  if (statusCode === 401 || statusCode === 403 || lower.includes("permission denied") || lower.includes("access denied") || lower.includes("forbidden") || combined.includes("无权访问") || combined.includes("权限")) {
    return "暂无该报表所需数据权限，无法执行本次查询。请联系报表创建人或管理员为你开通相关数据表权限后重试。";
  }
  const cleaned = rawMessage.replace(/Request failed with status code\s+\d+/i, "").trim();
  return cleaned || "报表执行失败，暂时无法获取结果。请稍后重试，或联系管理员检查报表配置与数据权限。";
};
