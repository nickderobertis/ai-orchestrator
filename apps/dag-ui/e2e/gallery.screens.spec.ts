import { mkdirSync, writeFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { runs } from "./fixture-facts";
import { VIEWPORTS, type Viewport } from "./viewports";

/**
 * Every major surface of the DAG Observatory, captured at every viewport in the
 * matrix, against the same fixture server and Vite stack the browser e2e drives.
 *
 * This tier asserts almost nothing on purpose: it is the operator's eyes. Polish
 * problems — a column that crowds its neighbour, a panel clipped at one width, a
 * control that has nowhere to go — are invisible to an assertion that was never
 * written for them, and until now the only way to see one was to start the app by
 * hand at each size. The waits below are what make a capture worth looking at: each
 * surface is photographed once its real reads have landed, never mid-skeleton.
 *
 * `screenshots.config.ts` selects this file and nothing else; `just dag-ui-screens`
 * is how it runs, and the gallery it writes is per invocation so two operators (or
 * two agents) capturing at once do not overwrite each other's images.
 */

/**
 * Where this run writes its images, as `just dag-ui-screens` named it.
 *
 * The recipe makes the directory and passes it in, which is what lets it print the path
 * before Playwright has said anything and what keeps two concurrent captures apart.
 * `scripts/dag-ui-screens.sh` is the only source of that path — this tier refuses to
 * run without it rather than inventing a second place galleries can land, and the path
 * has to be absolute because a relative one would scatter images wherever the process
 * happened to start.
 */
const galleryDirectory = (): string => {
  const named = process.env.DAG_UI_SCREENSHOT_DIR;
  if (named === undefined || !isAbsolute(named)) {
    throw new Error(
      `DAG_UI_SCREENSHOT_DIR must name an absolute gallery directory, not ${named ?? "nothing"}; run this tier through 'just dag-ui-screens', which makes one per invocation`,
    );
  }
  mkdirSync(named, { recursive: true });
  return named;
};

const gallery = galleryDirectory();

/** One photographed surface: how to reach it, and how to know it has finished loading. */
interface Surface {
  readonly name: string;
  readonly title: string;
  readonly open: (page: Page) => Promise<void>;
}

const SURFACES: readonly Surface[] = [
  {
    name: "01-run-list-overall",
    title: "Run list and the overall view",
    open: async (page) => {
      await page.goto(`/?run=${runs().live}&view=overall`);
      await expect(page.getByText("DAG Observatory")).toBeVisible();
      await expect(page.locator(".metric")).toHaveCount(4);
      await expect(
        page.getByRole("region", { name: "Graph timeline" }),
      ).toBeVisible();
    },
  },
  {
    name: "02-graph-rows",
    title: "Overall view: one row per node, plus the run's own",
    open: async (page) => {
      await page.goto(`/?run=${runs().live}&view=overall`);
      await page
        .getByRole("region", { name: "Graph timeline" })
        .getByRole("button", { name: "Expand timeline" })
        .click();
      // Photographed with the rows drawn, and with one of them opened again into
      // the category lanes that are the third level of this reading.
      const dashboard = page.getByRole("region", {
        name: "dashboard timeline",
      });
      await dashboard.getByRole("button", { name: "Expand timeline" }).click();
      await expect(
        dashboard.getByTestId("timeline-lane").first(),
      ).toBeVisible();
    },
  },
  {
    name: "03-run-level-session",
    title: "Overall view with a run-level session open over it",
    open: async (page) => {
      await page.goto(`/?run=${runs().live}&view=overall`);
      await page
        .getByRole("region", { name: "Graph timeline" })
        .getByRole("button", { name: "Expand timeline" })
        .click();
      await page
        .getByRole("region", { name: "Run-level timeline" })
        .getByRole("button", { name: /^Run-level · Orchestrator/ })
        .click();
      await expect(
        page
          .getByRole("region", { name: "Timeline item detail" })
          .getByRole("article", { name: /^Turn / })
          .first(),
      ).toBeVisible();
    },
  },
  {
    name: "04-graph",
    title: "Graph view",
    open: async (page) => {
      await page.goto(`/?run=${runs().live}&view=graph`);
      await expect(page.locator(".dag-node.state-running")).toContainText(
        "dashboard",
      );
      await expect(
        page.locator(".dag-node.state-failed").first(),
      ).toBeVisible();
    },
  },
  {
    name: "05-node-collapsed",
    title: "Node view: the collapsed line over its transcript",
    open: async (page) => {
      await page.goto(`/?run=${runs().live}&node=dashboard`);
      await expect(
        page.getByRole("region", { name: "Timeline for dashboard" }),
      ).toBeVisible();
      await expect(
        page
          .getByRole("region", { name: "Node timeline" })
          .getByRole("button", { name: /engineer-dashboard/ }),
      ).toBeVisible();
      await expect(
        page
          .getByRole("region", { name: "Node transcript" })
          .getByRole("article")
          .first(),
      ).toBeVisible();
    },
  },
  {
    name: "06-node-expanded",
    title: "Node view: one row per category",
    open: async (page) => {
      await page.goto(`/?run=${runs().live}&node=dashboard`);
      const plot = page.getByRole("region", { name: "Node timeline" });
      await plot.getByRole("button", { name: "Expand timeline" }).click();
      // Photographed with the lanes drawn, not with the line they replaced.
      await expect(plot.getByRole("button", { name: /^Judge/ })).toBeVisible();
    },
  },
  {
    name: "07-node-item-detail",
    title: "Node view with a verification open over the reading",
    open: async (page) => {
      await page.goto(`/?run=${runs().live}&node=foundation`);
      await page
        .getByRole("region", { name: "Node timeline" })
        .getByRole("button", { name: /branch push/ })
        .click();
      await expect(
        page.getByRole("region", { name: "Timeline item detail" }),
      ).toContainText("Verification record");
    },
  },
  {
    name: "08-conversation",
    title: "A conversation in the right panel",
    open: async (page) => {
      await page.goto(`/?run=${runs().live}&node=dashboard`);
      await page
        .getByRole("region", { name: "Node transcript" })
        .getByRole("button", { name: /^Open Judge/ })
        .click();
      await expect(
        page
          .getByRole("region", { name: "Timeline item detail" })
          .getByRole("article", { name: /^Turn / })
          .first(),
      ).toBeVisible();
    },
  },
];

const fileName = (surface: Surface, size: Viewport): string =>
  `${surface.name}-${size.name}.png`;

for (const size of VIEWPORTS) {
  test.describe(`at ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height } });

    for (const surface of SURFACES) {
      test(`captures ${surface.title}`, async ({ page }) => {
        await surface.open(page);
        await page.screenshot({
          path: join(gallery, fileName(surface, size)),
          fullPage: true,
        });
      });
    }
  });
}

/**
 * The contact sheet, written once every image exists.
 *
 * A directory of twenty-five PNGs is not a gallery an operator reads; one page that
 * puts every viewport of one surface in a row is, because the whole point of this
 * tier is comparing widths against each other rather than looking at one of them.
 */
test.afterAll(() => {
  const rows = SURFACES.map(
    (surface) => `      <section>
        <h2>${surface.title}</h2>
        <div class="row">
${VIEWPORTS.map(
  (size) => `          <figure>
            <a href="${fileName(surface, size)}"
              ><img alt="${surface.title} at ${size.name}" src="${fileName(surface, size)}"
            /></a>
            <figcaption>${size.name}</figcaption>
          </figure>`,
).join("\n")}
        </div>
      </section>`,
  ).join("\n");
  writeFileSync(
    join(gallery, "index.html"),
    `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>DAG Observatory screenshots</title>
    <style>
      body { margin: 0; padding: 24px; background: #101014; color: #e8e8ef;
             font: 14px/1.5 system-ui, sans-serif; }
      h1 { font-size: 18px; }
      h2 { font-size: 14px; font-weight: 600; }
      .row { display: flex; gap: 16px; overflow-x: auto; align-items: flex-start; }
      figure { margin: 0; flex: 0 0 auto; }
      img { display: block; width: 360px; border: 1px solid #34343f; border-radius: 6px; }
      figcaption { padding-top: 6px; color: #9a9aa8; font-size: 12px; }
    </style>
  </head>
  <body>
    <h1>DAG Observatory — ${SURFACES.length} surfaces × ${VIEWPORTS.length} viewports</h1>
${rows}
  </body>
</html>
`,
    "utf8",
  );
  process.stdout.write(`dag-ui screens: gallery at ${gallery}/index.html\n`);
});
