import { type RefCallback, useCallback, useLayoutEffect, useRef } from "react";

/** How near the last line still counts as reading it, in CSS pixels. */
const BOTTOM_SLACK = 32;

/**
 * The scroller a panel's content sits in: the design system's own scroll viewport,
 * or the element the content was placed in when it is scrolled directly.
 */
function scrollerOf(content: HTMLElement | null): HTMLElement | null {
  if (content === null) return null;
  return (
    content.closest<HTMLElement>('[data-slot="scroll-area-viewport"]') ??
    content.parentElement
  );
}

/**
 * Follow a list that is still being written, and only while the reader is at its end.
 *
 * Two things are deliberately not followed. A *first* load is not an append: opening a
 * long transcript lands the reader at its beginning, and jumping them to the last turn
 * of a session they have not started reading is not what "keep up with the live one"
 * means. Neither is a change of `key` — that is a different list, read from its own
 * beginning. Everything else is an append, and it moves the view only when the reader
 * was already within `BOTTOM_SLACK` of the bottom, which is what makes scrolling up to
 * read something safe while the run keeps writing underneath.
 *
 * Returns the ref to put on the scrolled content; the scroller itself is found from
 * it, because it belongs to the design system's `ScrollArea` and is not ours to hold.
 */
export function useStickyBottom(
  key: string | undefined,
  count: number,
): RefCallback<HTMLElement> {
  const scroller = useRef<HTMLElement | null>(null);
  const atBottom = useRef(true);
  const seen = useRef({ key, count });

  const measure = useCallback(() => {
    const element = scroller.current;
    if (element === null) return;
    atBottom.current =
      element.scrollHeight - element.scrollTop - element.clientHeight <=
      BOTTOM_SLACK;
  }, []);

  const contentRef = useCallback<RefCallback<HTMLElement>>(
    (content) => {
      scroller.current?.removeEventListener("scroll", measure);
      scroller.current = scrollerOf(content);
      scroller.current?.addEventListener("scroll", measure, { passive: true });
      measure();
    },
    [measure],
  );

  useLayoutEffect(() => {
    const previous = seen.current;
    seen.current = { key, count };
    const appended =
      previous.key === key && previous.count > 0 && count > previous.count;
    const element = scroller.current;
    if (!appended || element === null || !atBottom.current) return;
    element.scrollTop = element.scrollHeight;
  }, [key, count]);

  return contentRef;
}
