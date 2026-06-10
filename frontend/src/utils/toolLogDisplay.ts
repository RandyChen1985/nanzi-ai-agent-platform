export const SQL_TOOL_RESULT_DELIMITER = "--- 结果 ---";
export const SQL_TOOL_ERROR_DELIMITER = "--- 错误 ---";

export type SqlToolLogBodyKind = "result" | "error";

export type SqlToolLogSections = {
  sqlPart: string;
  bodyPart: string;
  bodyKind: SqlToolLogBodyKind;
  trailingPart: string;
};

const SQL_TOOL_BODY_DELIMITERS: Array<{ delimiter: string; bodyKind: SqlToolLogBodyKind }> = [
  { delimiter: SQL_TOOL_RESULT_DELIMITER, bodyKind: "result" },
  { delimiter: SQL_TOOL_ERROR_DELIMITER, bodyKind: "error" },
];

/** 拆分 ChatBI execute_sql_query 日志：SQL / 结果或错误 / [系统检测] 后缀 */
export function splitSqlToolLogDetails(details: string): SqlToolLogSections | null {
  for (const { delimiter, bodyKind } of SQL_TOOL_BODY_DELIMITERS) {
    const delimIdx = details.indexOf(delimiter);
    if (delimIdx === -1) continue;

    const sqlPart = details.slice(0, delimIdx).trim();
    const after = details.slice(delimIdx + delimiter.length);
    const detectIdx = after.search(/\n\n\[系统检测\]/);

    if (detectIdx >= 0) {
      return {
        sqlPart,
        bodyPart: after.slice(0, detectIdx).trim(),
        bodyKind,
        trailingPart: after.slice(detectIdx).trim(),
      };
    }

    return {
      sqlPart,
      bodyPart: after.trim(),
      bodyKind,
      trailingPart: "",
    };
  }

  return null;
}

export function sqlToolLogBodyLabel(bodyKind: SqlToolLogBodyKind): string {
  return bodyKind === "error" ? "Error" : "Query Result";
}

export function isSqlLikeToolLogDetails(details: string): boolean {
  return (
    details.includes("SELECT ")
    || details.includes("[Executed SQL]:")
    || details.includes("SQL:")
  );
}
