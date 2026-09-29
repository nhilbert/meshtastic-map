// Shared navigation keeps tools reachable when the rail becomes a drawer.
import { $ } from "./util.js";

const compact = matchMedia("(max-width: 700px)");
let returnFocus = null;

export function setRail(open, restoreFocus = true) {
  const wasOpen = document.body.classList.contains("rail-open");
  if (open && !wasOpen) returnFocus = document.activeElement;
  document.body.classList.toggle("rail-open", open);
  $("#btnRail").setAttribute("aria-expanded", String(open));
  $("#railBackdrop").hidden = !open || !compact.matches;
  $("#rail").inert = compact.matches && !open;
  // The header stays available; the covered map and inspector do not receive focus.
  $("#view").inert = compact.matches && open;
  $("#right").inert = compact.matches && open;
  if (compact.matches && open) $("#btnCloseRail").focus();
  else if (wasOpen && restoreFocus && returnFocus?.isConnected) returnFocus.focus();
}

export function showSection(name) {
  const section = document.querySelector(`details[data-sec="${name}"]`);
  if (!section) return;
  if (compact.matches) setRail(true);
  section.open = true;
  section.querySelector("summary").focus();
  section.scrollIntoView({ block: "nearest" });
}

export function initWorkspace() {
  $("#btnRail").addEventListener("click", () => setRail(!document.body.classList.contains("rail-open")));
  $("#btnCloseRail").addEventListener("click", () => setRail(false));
  $("#railBackdrop").addEventListener("click", () => setRail(false));
  $("#btnDevice").addEventListener("click", () => showSection("device"));
  compact.addEventListener("change", () => setRail(false, false));
  setRail(false, false);
}
