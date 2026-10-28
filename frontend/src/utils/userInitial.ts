/**
 * 用户头像简称：左下角 / 个人中心这类小头像里只放两个字。
 *
 * 口径（用户确认）：
 * - 英文名取前两个字母（"demo" → "DE"）；
 * - 中文名取后两个汉字（"李世民" → "世民"、"曹老板" → "老板"）；
 * - 只有两个字时取整名——"后两个"本来就是全部，不需要单独特判。
 *
 * 两个细节值得说明：
 * 1. 英文侧先剔掉空格与标点，"Eric Wang" 取 "ER" 而不是 "E "；
 * 2. 中文侧只数汉字，"李 世民" 仍是 "世民"，不会被空格当成一个字。
 *
 * 姓名缺失时回退（默认 "U"），否则头像会出现空白方块。
 */
const HAN = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/u;
const LATIN = /[A-Za-z0-9\u00C0-\u024F]/;
const VISIBLE = /[\p{L}\p{N}\p{Extended_Pictographic}]/u;

export function userInitialOf(name?: string | null, fallback = "U"): string {
  const value = String(name ?? "").replace(/\s+/g, " ").trim();
  if (!value) return fallback;

  // Array.from 按码点切分：emoji 或代理对不会被截成半个字符
  const chars = Array.from(value);
  const han = chars.filter((ch) => HAN.test(ch));
  if (han.length > 0) return han.slice(-2).join("");

  const latin = chars.filter((ch) => LATIN.test(ch));
  const picked = (latin.length > 0 ? latin : chars.filter((ch) => VISIBLE.test(ch)))
    .slice(0, 2)
    .join("");
  return (picked || fallback).toUpperCase();
}
