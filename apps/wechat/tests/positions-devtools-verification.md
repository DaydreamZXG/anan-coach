# Positions DevTools integration checklist

This checklist deliberately records integration verification only. RU-RES-02 does not make
preventive runtime changes to the positions pages without a focused failing test.

## Session and late-response checks

- Open `pages/positions/index`, delay its network responses, switch to another tab, and then let
  the responses settle. Confirm that no hidden-page loading or error projection appears when the
  positions tab is opened again.
- Repeat the delayed-response check for search, compare, detail, goal, and briefing pages while
  navigating back quickly. Confirm that the destination page is not overwritten by the previous
  page instance.
- Keep the same user and access token, rotate the canonical auth generation, then release an old
  delayed response. Confirm that the old response does not render into the current page.
- Switch from account A to account B while a positions request is delayed. Confirm that no title,
  filters, comparison selection, goal, or briefing data from account A appears for account B.

## Overlay and navigation checks

- Open every filter, picker, confirmation sheet, or modal reachable from positions search and
  compare. Navigate forward and back from each action, then tap and scroll the returned page.
  Confirm that no transparent mask intercepts touch events.
- Exercise back navigation from detail, compare, goal, and briefing with both a normal page stack
  and a cold-entry stack. Confirm that the registered fallback route is used and no overlay remains.

## Evidence to retain

- Record the DevTools route, account/session transition, delayed request, and final visible state.
- Retain a screenshot only when a stale projection or touch mask is reproduced; add a focused
  automated test before changing positions runtime code.
