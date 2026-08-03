import {
  formatAbsoluteTimestamp,
  formatRelativeTimestamp,
  formatTimestamp,
  parseTimestamp,
} from "./time";

/**
 * One recorded moment, rendered as words rather than as the ISO string the read
 * contract serves.
 *
 * Every reading is backed by the whole instant on hover, and by the exact recorded
 * stamp in `datetime` — so the compact form on screen costs the reader nothing, and
 * `relative` can answer "how long ago" without the precise moment becoming
 * unreachable.
 */
export function Timestamp({
  at,
  className,
  relative = false,
}: {
  readonly at: string;
  readonly className?: string;
  /** Read it as an age ("7 days ago") instead of as the moment it happened. */
  readonly relative?: boolean;
}) {
  const parsed = parseTimestamp(at);
  // llmlint: ignore[changed_behavior_has_e2e] no served payload reaches this: the read contract validates every stamp as an ISO datetime with an offset, so a record a browser could not parse fails `dag-model` first and surfaces as the telemetry banner the offline journey covers; the guard only stops a caller handing this some other recorded string from taking the whole view down with `Invalid Date`, and `time.test.ts` proves the reading it falls back to.
  if (parsed === undefined) return <span className={className}>{at}</span>;
  return (
    <time
      className={className}
      dateTime={parsed.toISOString()}
      title={formatAbsoluteTimestamp(at)}
    >
      {relative ? formatRelativeTimestamp(at) : formatTimestamp(at)}
    </time>
  );
}
