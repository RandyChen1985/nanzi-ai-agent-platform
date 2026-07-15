export interface SavedReportRunTarget {
  params_schema?: Array<{ type?: string; name?: string }>;
}

export interface SavedReportRunFormValue {
  dateRange: string;
  startDate: string;
  endDate: string;
  monthRange: string;
  startMonth: string;
  endMonth: string;
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
      params_schema: [{ name: "date_range", type: "date_range", label: "日期范围", default: "month_start_to_today", options: ["today", "yesterday", "last_7_days", "month_start_to_today", "custom_range"] }],
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
  const maxDisplayRows = 150;
  const displayRows = rows.slice(0, maxDisplayRows);
  let markdown = `\n\n| ${columns.join(" | ")} |\n| ${columns.map(() => "---").join(" | ")} |\n`;
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
  if (rows.length > maxDisplayRows) markdown += `\n> *⚠️ 结果集数据量较大，已在聊天框中自动为您省略后半部分（共展示前 ${maxDisplayRows} 行 / 总计 ${rows.length} 行）。*`;
  return markdown;
};

export const buildSavedReportRunParams = (
  report: SavedReportRunTarget | null | undefined,
  form: SavedReportRunFormValue,
) => {
  const usesMonthRange = Boolean(report?.params_schema?.some((item) => item?.type === "month_range" || item?.name === "month_range"));
  if (usesMonthRange) {
    const params: Record<string, any> = { month_range: form.monthRange };
    if (form.monthRange === "custom_month_range") {
      params.start_month = form.startMonth;
      params.end_month = form.endMonth;
    }
    return params;
  }
  const params: Record<string, any> = { date_range: form.dateRange };
  if (form.dateRange === "custom_range") {
    params.start_date = form.startDate;
    params.end_date = form.endDate;
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
