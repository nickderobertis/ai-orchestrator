import {
  format,
  formatDistanceToNowStrict,
  hoursToMilliseconds,
  isThisYear,
  isToday,
  isValid,
  millisecondsToHours,
  millisecondsToMinutes,
  millisecondsToSeconds,
  minutesToMilliseconds,
  parseISO,
  secondsToMilliseconds,
} from "date-fns";

/**
 * A duration in the largest units that keep it readable, tiered by how long it ran:
 * `420ms` under a second, `42s` under a minute, `12m 4s` under an hour, and
 * `2h 5m 10s` beyond one.
 */
export function formatDuration(durationMs: number): string {
  const total = Math.max(0, Math.round(durationMs));
  if (total < secondsToMilliseconds(1)) return `${total}ms`;
  // Hours are counted off the total rather than read from a calendar breakdown: a
  // node that ran for two days is thirty-odd hours of work, not "1 day 8 hours".
  const hours = millisecondsToHours(total);
  const minutes = millisecondsToMinutes(total - hoursToMilliseconds(hours));
  const seconds = millisecondsToSeconds(
    total - hoursToMilliseconds(hours) - minutesToMilliseconds(minutes),
  );
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

/** The same reading for the durations the read contract serves in seconds. */
export function formatDurationSeconds(seconds: number): string {
  return formatDuration(secondsToMilliseconds(seconds));
}

/**
 * A recorded stamp as a `Date`, or `undefined` when what was recorded is not one.
 *
 * Nothing invents a date for an unreadable record: every formatter below hands the
 * recorded text back unchanged instead, so a journal a peer wrote in some other shape
 * is visible as what it says rather than as "Invalid Date".
 */
export function parseTimestamp(at: string): Date | undefined {
  const parsed = parseISO(at);
  return isValid(parsed) ? parsed : undefined;
}

/**
 * A stamp as an operator scanning a column reads it: today's work is a clock time,
 * and anything older carries the day — and the year, once it is not this one — so a
 * row can never be read as having happened today when it did not.
 */
export function formatTimestamp(at: string): string {
  const parsed = parseTimestamp(at);
  if (parsed === undefined) return at;
  if (isToday(parsed)) return format(parsed, "HH:mm:ss");
  // llmlint: ignore[changed_behavior_has_e2e] the browser fixture's journal is written by the run that serves it, so every stamp a journey can reach is today's and the dated branches are reachable only by moving the clock, which a live server tier cannot do; the e2e journey asserts every shape this returns across a whole rail, and `time.test.ts` pins this branch against a fixed instant.
  if (isThisYear(parsed)) return format(parsed, "MMM d, HH:mm:ss");
  // llmlint: ignore[changed_behavior_has_e2e] same reason: no served run can carry a stamp from another year, so `time.test.ts` pins this branch against a fixed instant instead.
  return format(parsed, "MMM d yyyy, HH:mm:ss");
}

/** The whole instant, zone included: what a compact reading is backed by on hover. */
export function formatAbsoluteTimestamp(at: string): string {
  const parsed = parseTimestamp(at);
  return parsed === undefined ? at : format(parsed, "PPpp zzzz");
}

/** How long ago it happened — the reading that answers "is this still recent?". */
export function formatRelativeTimestamp(at: string): string {
  const parsed = parseTimestamp(at);
  return parsed === undefined
    ? at
    : formatDistanceToNowStrict(parsed, { addSuffix: true });
}
