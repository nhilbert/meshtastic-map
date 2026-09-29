// Navigation behavior without a browser. Run: node --test tests/ui_workspace.test.mjs
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

// Only the DOM operations used by workspace navigation are modeled here.
class Element {
  constructor() {
    this.attrs = {}; this.events = {}; this.isConnected = true;
    this.hidden = false; this.inert = false;
  }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener(key, callback) { this.events[key] = callback; }
  focus() { document.activeElement = this; }
  scrollIntoView() { this.scrolled = true; }
  querySelector() { return this.summary; }
}
const compact = { matches: true, addEventListener(_, callback) { this.change = callback; } };
globalThis.matchMedia = () => compact;
globalThis.proj4 = { defs() {} };
if (!globalThis.navigator) globalThis.navigator = { language: "en" };
const { initWorkspace, setRail, showSection } = await import("../webmap/js/workspace.js");
let elements, classes;
beforeEach(() => {
  compact.matches = true;
  elements = Object.fromEntries(["#btnRail", "#railBackdrop", "#rail", "#view", "#right",
    "#btnCloseRail", "#btnDevice"].map(id => [id, new Element()]));
  classes = new Set();
  globalThis.document = {
    activeElement: elements["#btnRail"],
    querySelector: selector => elements[selector],
    body: { classList: {
      contains: name => classes.has(name),
      toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
    } },
  };
  initWorkspace();
});

test("closed mobile controls cannot receive keyboard focus", () => {
  assert.equal(elements["#rail"].inert, true);
  assert.equal(elements["#view"].inert, false);
  assert.equal(elements["#railBackdrop"].hidden, true);
  assert.equal(elements["#btnRail"].attrs["aria-expanded"], "false");
});

test("drawer blocks covered content and returns focus to its opener", () => {
  const opener = elements["#btnDevice"];
  opener.focus(); setRail(true);
  assert.equal(document.activeElement, elements["#btnCloseRail"]);
  assert.equal(elements["#rail"].inert, false);
  assert.equal(elements["#view"].inert, true);
  assert.equal(elements["#right"].inert, true);
  assert.equal(elements["#railBackdrop"].hidden, false);
  elements["#railBackdrop"].events.click();
  assert.equal(document.activeElement, opener);
  assert.equal(elements["#view"].inert, false);
  assert.equal(elements["#right"].inert, false);
});

test("section shortcuts reveal and focus a folded section on mobile", () => {
  const section = new Element(); section.summary = new Element();
  elements['details[data-sec="jobs"]'] = section;
  showSection("jobs");
  assert.equal(section.open, true);
  assert.equal(section.scrolled, true);
  assert.equal(document.activeElement, section.summary);
  assert.equal(elements["#btnRail"].attrs["aria-expanded"], "true");
});

test("switching to desktop clears the backdrop and restores content access", () => {
  setRail(true); compact.matches = false; compact.change();
  assert.equal(classes.has("rail-open"), false);
  for (const id of ["#rail", "#view", "#right"]) assert.equal(elements[id].inert, false);
  assert.equal(elements["#railBackdrop"].hidden, true);
});

test("map picking can close the drawer without restoring focus to its hidden form", () => {
  setRail(true);
  const mapControl = new Element(); mapControl.focus();
  setRail(false, false);
  assert.equal(document.activeElement, mapControl);
  assert.equal(elements["#rail"].inert, true);
});
