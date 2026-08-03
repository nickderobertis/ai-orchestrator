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
  // A record no clock can read is shown exactly as it was recorded: a `<time>` around
  // it would be a claim about an instant this journal never wrote.
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
