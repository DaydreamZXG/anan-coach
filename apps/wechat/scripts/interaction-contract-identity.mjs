import assert from "node:assert/strict";

export const interactionEventKinds = Object.freeze({
  TAP: "tap",
  CONFIRM: "confirm",
  ACTION: "action",
});

export function assertSelectorBindsHandler({ route, wxml, selector, handler, eventKind = "tap" }) {
  assert.ok(
    Object.values(interactionEventKinds).includes(eventKind),
    `${route} has unsupported interaction event kind: ${eventKind}`,
  );
  const selectorElements = openingElements(wxml).filter((element) =>
    elementMatchesSelector(element, selector),
  );

  assert.ok(selectorElements.length > 0, `${route} WXML does not expose ${selector}`);
  const eventAttributes = [`bind${eventKind}`, `bind:${eventKind}`];
  assert.ok(
    selectorElements.some((element) =>
      eventAttributes.some((attribute) => element.attributes.get(attribute) === handler),
    ),
    `${route} selector ${selector} must bind${eventKind} ${handler} on the same element`,
  );
}

export function hasClassToken(wxml, className) {
  return openingElements(wxml).some((element) =>
    (element.attributes.get("class") ?? "").split(/\s+/u).includes(className),
  );
}

function openingElements(wxml) {
  const elements = [];
  let cursor = 0;

  while (cursor < wxml.length) {
    const start = wxml.indexOf("<", cursor);
    if (start === -1) break;
    if (!/[A-Za-z]/u.test(wxml[start + 1] ?? "")) {
      cursor = start + 1;
      continue;
    }

    const nameMatch = /^[A-Za-z][A-Za-z0-9-]*/u.exec(wxml.slice(start + 1));
    if (!nameMatch) {
      cursor = start + 1;
      continue;
    }

    const attributeStart = start + 1 + nameMatch[0].length;
    const end = findOpeningTagEnd(wxml, attributeStart);
    if (end === -1) break;
    elements.push({
      name: nameMatch[0],
      attributes: parseAttributes(wxml.slice(attributeStart, end)),
    });
    cursor = end + 1;
  }

  return elements;
}

function elementMatchesSelector(element, selector) {
  if (selector.startsWith(".")) {
    return (element.attributes.get("class") ?? "").split(/\s+/u).includes(selector.slice(1));
  }
  return element.name === selector;
}

function findOpeningTagEnd(source, start) {
  let quote = null;
  for (let index = start; index < source.length; index += 1) {
    const character = source[index];
    if (quote) {
      if (character === quote) quote = null;
    } else if (character === '"' || character === "'") {
      quote = character;
    } else if (character === ">") {
      return index;
    }
  }
  return -1;
}

function parseAttributes(source) {
  const attributes = new Map();
  let cursor = 0;
  while (cursor < source.length) {
    while (/\s/u.test(source[cursor] ?? "")) cursor += 1;
    if (source[cursor] === "/") {
      cursor += 1;
      continue;
    }

    const nameMatch = /^[A-Za-z_:][A-Za-z0-9:_.-]*/u.exec(source.slice(cursor));
    if (!nameMatch) {
      cursor += 1;
      continue;
    }
    const name = nameMatch[0];
    cursor += name.length;
    while (/\s/u.test(source[cursor] ?? "")) cursor += 1;
    if (source[cursor] !== "=") {
      attributes.set(name, "");
      continue;
    }

    cursor += 1;
    while (/\s/u.test(source[cursor] ?? "")) cursor += 1;
    const quote = source[cursor];
    if (quote === '"' || quote === "'") {
      cursor += 1;
      const valueStart = cursor;
      while (cursor < source.length && source[cursor] !== quote) cursor += 1;
      attributes.set(name, source.slice(valueStart, cursor));
      if (source[cursor] === quote) cursor += 1;
      continue;
    }

    const valueStart = cursor;
    while (cursor < source.length && !/\s/u.test(source[cursor]) && source[cursor] !== "/") {
      cursor += 1;
    }
    attributes.set(name, source.slice(valueStart, cursor));
  }
  return attributes;
}
