import assert from "node:assert/strict";
import test from "node:test";

import { assertSelectorBindsHandler, hasClassToken } from "./interaction-contract-identity.mjs";

// This suite is intentionally named *.node-test.mjs so Vitest does not collect it.

const route = "pages/profile/qualification/index";
const selector = ".s9-primary-button";

test("accepts a class selector bound to its declared handler", () => {
  assert.doesNotThrow(() =>
    assertSelectorBindsHandler({
      route,
      selector,
      handler: "next",
      wxml: '<button class="s9-primary-button" bindtap="next">继续</button>',
    }),
  );
});

test("accepts single-quoted event attributes in any order", () => {
  assert.doesNotThrow(() =>
    assertSelectorBindsHandler({
      route,
      selector,
      handler: "next",
      wxml: "<button bindtap = 'next' class='s9-primary-button cta'>继续</button>",
    }),
  );
});

test("accepts a confirm interaction on the selected input that binds its handler", () => {
  assert.doesNotThrow(() =>
    assertSelectorBindsHandler({
      route: "pages/private-knowledge/index",
      selector: ".library-search__input",
      handler: "search",
      eventKind: "confirm",
      wxml: '<input class="library-search__input" bindconfirm="search" />',
    }),
  );
});

test("accepts a custom component action event on the selected component", () => {
  assert.doesNotThrow(() =>
    assertSelectorBindsHandler({
      route: "pages/positions/index",
      selector: "state-view",
      handler: "openQualificationProfile",
      eventKind: "action",
      wxml: '<state-view state="empty" bind:action="openQualificationProfile" />',
    }),
  );
});

test("rejects a selected element with the wrong handler", () => {
  assert.throws(
    () =>
      assertSelectorBindsHandler({
        route,
        selector,
        handler: "next",
        wxml: '<button class="s9-primary-button" bindtap="previous">返回</button>',
      }),
    /selector .s9-primary-button must bindtap next on the same element/u,
  );
});

test("rejects a confirm contract that uses bindtap", () => {
  assert.throws(
    () =>
      assertSelectorBindsHandler({
        route: "pages/private-knowledge/index",
        selector: ".library-search__input",
        handler: "search",
        eventKind: "confirm",
        wxml: '<button class="library-search__input" bindtap="search">搜索</button>',
      }),
    /selector .library-search__input must bindconfirm search on the same element/u,
  );
});

test("rejects a selector when the declared handler is on a different element", () => {
  assert.throws(
    () =>
      assertSelectorBindsHandler({
        route,
        selector,
        handler: "next",
        wxml: '<button class="s9-primary-button" bindtap="previous">返回</button><button bindtap="next">继续</button>',
      }),
    /selector .s9-primary-button must bindtap next on the same element/u,
  );
});

test("rejects a confirm selector when another element owns the handler", () => {
  assert.throws(
    () =>
      assertSelectorBindsHandler({
        route: "pages/private-knowledge/index",
        selector: ".library-search__input",
        handler: "search",
        eventKind: "confirm",
        wxml: '<input class="library-search__input" bindinput="inputQuery" /><input bindconfirm="search" />',
      }),
    /selector .library-search__input must bindconfirm search on the same element/u,
  );
});

test("rejects a missing exact class selector", () => {
  assert.throws(
    () =>
      assertSelectorBindsHandler({
        route,
        selector,
        handler: "next",
        wxml: '<button bindtap="next">继续</button>',
      }),
    /WXML does not expose .s9-primary-button/u,
  );
});

test("does not accept selector text embedded in another attribute value", () => {
  assert.throws(
    () =>
      assertSelectorBindsHandler({
        route,
        selector,
        handler: "next",
        wxml: '<button aria-label="s9-primary-button" bindtap="next">继续</button>',
      }),
    /WXML does not expose .s9-primary-button/u,
  );
});

test("matches only exact class tokens", () => {
  assert.equal(hasClassToken('<view class="task-heading primary-task"></view>', "task"), false);
  assert.equal(hasClassToken("<view class='summary task active'></view>", "task"), true);
});

test("rejects an unknown interaction event kind", () => {
  assert.throws(
    () =>
      assertSelectorBindsHandler({
        route,
        selector,
        handler: "next",
        eventKind: "submit",
        wxml: '<button data-interaction-id="qualification-next" bindtap="next">继续</button>',
      }),
    /has unsupported interaction event kind: submit/u,
  );
});
