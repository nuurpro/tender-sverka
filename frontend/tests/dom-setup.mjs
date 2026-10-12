import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://127.0.0.1:3001", pretendToBeVisual: true,
});
for (const name of ["window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLButtonElement", "Element", "Node", "NodeFilter", "MutationObserver", "CustomEvent", "Event",
  "getComputedStyle", "requestAnimationFrame", "cancelAnimationFrame"]) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true, writable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
