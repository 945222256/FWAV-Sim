/* I-218：控制台时间只接受具有明确时区的时刻；来源不明确时保留待核对，
 * 不用浏览器所在时区补猜。采样有效性与活动时间共用同一服务器时钟。 */
export const SAMPLE_MAX_AGE_S = 20;

interface ParsedTime {
  ms: number | null;
  reason: string | null;
}

export function parseTimestamp(value: string | null | undefined): ParsedTime {
  if (!value) return { ms: null, reason: "未提供时间" };
  if (typeof value !== "string") return { ms: null, reason: "时间格式无效" };
  const iso = value.trim();
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(iso)) {
    return { ms: null, reason: "缺少时区" };
  }
  const parts = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/i.exec(iso);
  if (!parts) return { ms: null, reason: "时间格式无效" };
  const [, y, mon, day, hour, minute, second, , zoneHour, zoneMinute] = parts;
  const year = Number(y), month = Number(mon);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (month < 1 || month > 12 || Number(day) < 1 || Number(day) > days[month - 1]
      || Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59
      || Number(zoneHour ?? 0) > 23 || Number(zoneMinute ?? 0) > 59) {
    return { ms: null, reason: "时间格式无效" };
  }
  const ms = Date.parse(iso.replace(" ", "T"));
  return Number.isFinite(ms) ? { ms, reason: null } : { ms: null, reason: "时间格式无效" };
}

function duration(seconds: number): string {
  if (seconds < 60) return Math.floor(seconds) + " 秒";
  if (seconds < 3600) return Math.floor(seconds / 60) + " 分钟";
  return Math.floor(seconds / 3600) + " 小时";
}

export function timeAge(value: string | null | undefined, serverNow: number | null) {
  const parsed = parseTimestamp(value);
  if (parsed.ms == null) return { seconds: null, reason: parsed.reason };
  if (serverNow == null || !Number.isFinite(serverNow)) {
    return { seconds: null, reason: "服务器时钟未取得" };
  }
  const seconds = (serverNow - parsed.ms) / 1000;
  if (seconds < 0) {
    return { seconds: null, reason: "晚于服务器" + duration(Math.ceil(-seconds)) };
  }
  return { seconds, reason: null };
}

export function ago(value: string | null | undefined, serverNow: number | null): string {
  const age = timeAge(value, serverNow);
  return age.seconds == null ? "时间待核对（" + age.reason + "）" : duration(age.seconds) + "前";
}

export function sampleFreshness(value: string | null | undefined, serverNow: number | null) {
  const age = timeAge(value, serverNow);
  if (age.seconds == null) {
    return { status: "unknown" as const, message: "采样时间待核对（" + age.reason + "）" };
  }
  if (age.seconds > SAMPLE_MAX_AGE_S) {
    return { status: "stale" as const, message: "采样已过期（" + duration(age.seconds) + "前）" };
  }
  return { status: "fresh" as const, message: "采样有效" };
}

export function advanceServerClock(base: number | null, receivedAt: number | null, tick: number): number | null {
  if (base == null || receivedAt == null || !Number.isFinite(base)
      || !Number.isFinite(receivedAt) || !Number.isFinite(tick) || tick < receivedAt) return null;
  return base + tick - receivedAt;
}
