# UI review and improvement plan

## Selected visual direction

Signal Desk with Field Console telemetry (selected by the owner after reviewing three concepts):

- Slate surfaces, cyan primary actions, subtle cyan selections and 6 px controls.
- Quiet header tools and settings buttons; clear solid primary actions in forms and popups.
- One shared offline outline-icon set for navigation, sections and node actions.
- Compact mission readouts for distance, ETA and speed, plus a persistent device/packet strip.
- Dark by default, with saved light-mode preferences preserved. Base tiles are toned only in
  dark mode; overlays and semantic marker colors remain unchanged.
- Responsive drawers, keyboard navigation and all three translations remain supported.

Implemented in the existing app without additional libraries. Browser rendering remains
unverified in this session because no browser connection is available.

## Initial review

Reviewed 2026-09-29. Scope: the map application in `webmap/`, including tasks,
coordination, messaging and the inspector. Findings come from the implementation;
no browser connection was available for screenshots or interactive visual review.

## Five priorities and decisions

| Priority | Evidence and user impact | Options considered | Decision implemented |
| --- | --- | --- | --- |
| 1. Preserve map space on phones | Below 700 px, the sidebar occupied up to 40 vh, followed by a 55 vh map, plus the header and inspector. The workspace could exceed the viewport. | Keep stacking with smaller sections; replace the sidebar with a drawer. | Use a viewport-height workspace and a Controls drawer. Task, coordination and device shortcuts reveal their section. Map picking closes the drawer and returns to the form afterward. Details use a bottom overlay; opening messages clears that overlay on phones. |
| 2. Reduce initial visual density | Layers, tasks and coordination all started expanded in a 270 px rail. Secondary workflows competed with map controls. | Introduce a new navigation system; progressively disclose existing sections. | Keep the familiar structure, initially fold tasks and coordination, and retain saved section preferences. Strengthen section headings and layer spacing. Header shortcuts still open the relevant section directly. |
| 3. Expose connection state | Connection details were inside a collapsed Device section; the header's generic ready state described layer loading. | Expand Device permanently; add a compact status shortcut. | Show connected, disconnected, connecting, simulated or error state in a header button, with a dot and explicit text. Clicking reveals device controls. API failures replace stale connection status. |
| 4. Improve legibility and control consistency | Muted labels had low contrast, small buttons had tiny hit areas, search/time fields lacked shared styling, and disabled buttons looked clickable. | Increase all content sizes; adjust shared tokens and controls. | Darken muted light-theme text, brighten dark-theme text, standardize inputs and disabled states, and give coarse-pointer controls 44 px targets. Preserve the theme across reloads. |
| 5. Make keyboard navigation reliable | Inspector tabs lacked panel relationships and arrow-key behavior; rebuilding tabs discarded focus. Several icon controls lacked explicit names. | Add names only; implement the full navigation behavior. | Add tab/panel relationships, roving tab stops, Left/Right/Home/End navigation and focus preservation. Restore focus when closing details or the drawer, make covered panels inert, expose disclosure state, and provide common focus outlines and icon labels. |

## Implementation sequence

1. Add shared drawer navigation and wire every rail entry point.
2. Adjust responsive layout, section defaults and shared visual styles.
3. Surface device status and preserve theme choice.
4. Complete keyboard, focus and accessible naming behavior.
5. Update English/French catalogues, run checks and review the diff.

## Verification

- Passed: 94 Python tests (1 hardware test deselected), including translation
  completeness, stale-key and placeholder checks and fake-device regressions.
- Passed: syntax checks for all application JavaScript modules and repository Ruff lint.
- Passed: 5 Node navigation tests covering drawer access, focus return, section shortcuts,
  breakpoint changes and map-picking handoff.
- Visual acceptance remains outstanding: inspect 390 px, 768 px and desktop layouts,
  both themes, all three languages, keyboard-only use, messaging and map-pick flows
  with the simulated radio. Unit tests do not establish visual correctness.
