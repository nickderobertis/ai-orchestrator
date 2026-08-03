import "@testing-library/jest-dom/vitest";

// The views render every stamp in the reader's own zone, so the assertions on those
// words only mean something against a fixed one. Set before any test file — and so
// before any `Date` — is read.
process.env.TZ = "UTC";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  value: ResizeObserverStub,
});

Object.defineProperty(globalThis, "DOMMatrixReadOnly", {
  configurable: true,
  value: class {
    m22 = 1;
  },
});
