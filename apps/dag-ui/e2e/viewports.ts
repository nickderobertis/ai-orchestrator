/**
 * The screen sizes this app is held to, in one place.
 *
 * The operator iterates on this UI visually, and a polish problem that only appears
 * at one width is invisible until someone happens to open the app at it. So the
 * matrix is declared once and used twice: `gallery.screens.spec.ts` captures every
 * surface at every entry, and `dag-ui-navigation.spec.ts` drives the journeys whose
 * outcome depends on width at the two extremes of it.
 *
 * The five are the desktop sizes the app is actually read at, down to the smallest
 * laptop still in use, plus one phone — which is the only entry where the shell's
 * two columns stop being a comfortable fit and is therefore where every reflow and
 * scroll defect shows up first.
 */
export interface Viewport {
  /** How a captured file and a journey title name this size. */
  readonly name: string;
  readonly width: number;
  readonly height: number;
}

/** One entry, named by the size it is — so a label can never describe another size. */
const sized = (width: number, height: number): Viewport => ({
  name: `${width}x${height}`,
  width,
  height,
});

export const VIEWPORTS: readonly Viewport[] = [
  sized(1920, 1080),
  sized(1440, 900),
  sized(1280, 800),
  sized(1024, 768),
  sized(390, 844),
];

/** One entry of the matrix by name, so a journey names a size rather than restating it. */
export function viewport(name: string): Viewport {
  const found = VIEWPORTS.find((entry) => entry.name === name);
  if (found === undefined)
    throw new Error(
      `no viewport named ${name}; the matrix holds ${VIEWPORTS.map(({ name: known }) => known).join(", ")}`,
    );
  return found;
}

/** The widest entry: what an operator with a full screen sees. */
export const DESKTOP = viewport("1920x1080");
/** The narrowest entry: the phone the shell has to keep both columns usable at. */
export const PHONE = viewport("390x844");
