import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

/**
 * The reading a timeline segment carries, presented where it can actually be read.
 *
 * `@oneharness/ui`'s `Timeline` paints that reading as a fixed-width popover *inside*
 * its own `overflow-hidden` plot, at a fixed offset below the lane row it belongs to,
 * with no collision handling. A plot only has to be shorter than the popover — which
 * every collapsed line and every graph row is — for its bottom to be cut off, and the
 * clipping continues up the tree: the plot clips, the node view's pinned region clips,
 * and the overall view's scroll area clips. Widening any one of those only moves the
 * cut to the next, and un-clipping the plot alone trades the cut bottom for a popover
 * that leaves the screen sideways (measured at 241px past a 390px viewport).
 *
 * So the presentation moves out of that box entirely: the segment's own description
 * element stays exactly where the package puts it and keeps being what
 * `aria-describedby` resolves to, and this layer renders the same text — read from
 * that element, so there is still one source for it — into a fixed-position portal on
 * the document, placed against the segment and flipped or clamped to stay on screen.
 * The portal copy is `aria-hidden`: it is a second rendering of a description the
 * reader's assistive technology already has.
 */

/** How far the reading sits from its segment, and how close it may come to an edge. */
const OFFSET = 6;
const MARGIN = 8;

/** The prefix the package derives every segment description's id from. */
const DESCRIBED = '[aria-describedby^="timeline-detail-"]';

/** A segment being read, and the text the package composed for it. */
interface Anchored {
  readonly element: HTMLElement;
  readonly detail: string;
}

/**
 * The segment under an event, with its reading — the package's own text, read off the
 * element the segment's `aria-describedby` names, so this layer never composes a
 * second account of the same record.
 */
const anchoredAt = (target: EventTarget | null): Anchored | undefined => {
  const element = target instanceof Element ? target.closest(DESCRIBED) : null;
  if (!(element instanceof HTMLElement)) return undefined;
  const described = document.getElementById(
    element.getAttribute("aria-describedby") ?? "",
  );
  const detail = described?.textContent?.trim();
  return detail ? { element, detail } : undefined;
};

/** Keeps the previous value when the same segment is entered again, so nothing churns. */
const settle = (
  current: Anchored | undefined,
  next: Anchored | undefined,
): Anchored | undefined =>
  next !== undefined &&
  current?.element === next.element &&
  current.detail === next.detail
    ? current
    : next;

export function TimelinePopoverLayer() {
  const node = useRef<HTMLDivElement>(null);
  // Pointed at and focused are tracked apart, and either one shows the reading. The
  // package conflates them and clears on mouse-leave whichever it was, which loses a
  // keyboard reader's reading the moment a stationary pointer stops being over the
  // segment — and a region scrolling under that pointer does exactly that, so the
  // reading vanished from under a reader who had not touched the pointer at all.
  const [hovered, setHovered] = useState<Anchored>();
  const [focused, setFocused] = useState<Anchored>();
  const anchor = hovered ?? focused;

  const clear = useCallback(() => {
    setHovered(undefined);
    setFocused(undefined);
  }, []);

  // `pointerover` fires for every element the pointer enters, so one handler both
  // opens the reading over a segment and closes it on the way out — including the
  // move from one segment straight onto the next, which a pair of enter/leave
  // handlers has to reconcile between them.
  useEffect(() => {
    const point = (event: Event) =>
      setHovered((current) => settle(current, anchoredAt(event.target)));
    const focus = (event: Event) =>
      setFocused((current) => settle(current, anchoredAt(event.target)));
    // Focus moving to something that is not a segment ends the reading; focus moving
    // to another segment is the `focusin` above.
    const blur = (event: Event) =>
      setFocused((current) =>
        // Safely a `FocusEvent`: this handler is only ever reached through the
        // `focusout` registration below, which the DOM dispatches as one — the
        // parameter is the wider `Event` only because `addEventListener`'s listener
        // signature is untyped in the name it is registered under.
        settle(current, anchoredAt((event as FocusEvent).relatedTarget)),
      );
    const away = () => setHovered(undefined);
    document.addEventListener("pointerover", point);
    document.addEventListener("pointerleave", away);
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", blur);
    return () => {
      document.removeEventListener("pointerover", point);
      document.removeEventListener("pointerleave", away);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", blur);
    };
  }, []);

  /**
   * Put the reading beside its segment, and inside the screen.
   *
   * Written straight onto the element rather than held in state: the placement is
   * computed from the rendered size, so feeding it back through a render would either
   * loop or need an equality check to stop looping.
   */
  const place = useCallback(() => {
    const element = node.current;
    if (element === null || anchor === undefined) return;
    if (!anchor.element.isConnected) return clear();
    const segment = anchor.element.getBoundingClientRect();
    const reading = element.getBoundingClientRect();
    const rightmost = window.innerWidth - reading.width - MARGIN;
    const left = Math.min(
      Math.max(segment.left, MARGIN),
      Math.max(MARGIN, rightmost),
    );
    const below = segment.bottom + OFFSET;
    const above = segment.top - reading.height - OFFSET;
    // Below the segment where there is room, above it where there is not — and clamped
    // either way, because a segment can be off screen entirely: a region scrolled past
    // it, or the window shrank around it. Without the clamp the preferred side is only
    // bounded by where the segment went, which is off the top of the screen.
    const lowest = Math.max(
      MARGIN,
      window.innerHeight - reading.height - MARGIN,
    );
    const preferred =
      below + reading.height <= window.innerHeight - MARGIN ? below : above;
    const top = Math.min(Math.max(preferred, MARGIN), lowest);
    element.style.left = `${Math.round(left)}px`;
    element.style.top = `${Math.round(top)}px`;
    element.style.visibility = "visible";
  }, [anchor, clear]);

  // Before paint, so the reading is measured and placed rather than appearing at the
  // corner a fixed element starts in. `place` changes with the segment, which is the
  // only thing that moves it — and the segment and its text are set in one commit, so
  // the element exists to be measured by the time this runs.
  useLayoutEffect(place, [place]);
  // A plot sits inside regions that scroll, so the segment moves without any of this
  // changing; the reading follows it rather than being left behind pointing at
  // nothing. `capture`, because the scroll happens in those regions, not on the
  // document.
  useEffect(() => {
    if (anchor === undefined) return;
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [anchor, place]);

  if (anchor === undefined) return null;
  return createPortal(
    <div
      aria-hidden="true"
      className="timeline-popover"
      data-testid="timeline-popover"
      // A fresh element per reading, so it always starts hidden and is painted only
      // once the layout effect below has measured and placed it.
      key={anchor.detail}
      ref={node}
      style={{ visibility: "hidden" }}
    >
      {anchor.detail}
    </div>,
    document.body,
  );
}
