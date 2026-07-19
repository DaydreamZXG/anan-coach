import assert from "node:assert/strict";

/**
 * Developer Tools 2.01.2510290 can stop answering the legacy Page.getData/Page.setData
 * automation commands while App.callFunction remains healthy. Keep that compatibility
 * concern in one bridge instead of weakening visual and click assertions.
 */
export async function readPageData(program, page) {
  const payload = await program.evaluate(function readCurrentPageData(expectedRoute) {
    const pages = getCurrentPages();
    const current = pages[pages.length - 1];
    if (!current || current.route !== expectedRoute) {
      return JSON.stringify({
        __pageDataBridgeError: true,
        actualRoute: current?.route ?? null,
        expectedRoute,
      });
    }
    return JSON.stringify({ data: current.data });
  }, page.path);
  const result = parseBridgePayload(payload, `read ${page.path}`);
  assert.equal(
    result?.__pageDataBridgeError,
    undefined,
    `Page data bridge expected ${page.path} but found ${result?.actualRoute ?? "no page"}`,
  );
  return result.data;
}

export async function setPageData(program, page, data) {
  const payload = await program.evaluate(
    function setCurrentPageData(expectedRoute, nextDataJson) {
      const pages = getCurrentPages();
      const current = pages[pages.length - 1];
      if (!current || current.route !== expectedRoute) {
        return JSON.stringify({
          __pageDataBridgeError: true,
          actualRoute: current?.route ?? null,
          expectedRoute,
        });
      }
      const nextData = JSON.parse(nextDataJson);
      current.setData(nextData);
      return JSON.stringify({ applied: true });
    },
    page.path,
    JSON.stringify(data),
  );
  const result = parseBridgePayload(payload, `write ${page.path}`);
  assert.equal(
    result?.__pageDataBridgeError,
    undefined,
    `Page data bridge expected ${page.path} but found ${result?.actualRoute ?? "no page"}`,
  );
  assert.equal(result?.applied, true, `Page data bridge did not apply ${page.path}`);
}

export async function invokePageHandler(program, page, handler, dataset = {}) {
  const payload = await program.evaluate(
    function invokeCurrentPageHandler(expectedRoute, methodName, currentTargetDatasetJson) {
      const pages = getCurrentPages();
      const current = pages[pages.length - 1];
      if (!current || current.route !== expectedRoute) {
        return JSON.stringify({
          __pageDataBridgeError: true,
          actualRoute: current?.route ?? null,
          expectedRoute,
        });
      }
      if (typeof current[methodName] !== "function") {
        return JSON.stringify({
          __pageDataBridgeError: true,
          missingHandler: methodName,
          expectedRoute,
        });
      }
      const currentTargetDataset = JSON.parse(currentTargetDatasetJson);
      current[methodName]({
        currentTarget: { dataset: currentTargetDataset },
        target: { dataset: currentTargetDataset },
        detail: {},
      });
      return JSON.stringify({ invoked: true });
    },
    page.path,
    handler,
    JSON.stringify(dataset),
  );
  const result = parseBridgePayload(payload, `invoke ${page.path}.${handler}`);
  assert.equal(
    result?.__pageDataBridgeError,
    undefined,
    result?.missingHandler
      ? `${page.path} does not expose ${result.missingHandler}`
      : `Page data bridge expected ${page.path} but found ${result?.actualRoute ?? "no page"}`,
  );
  assert.equal(result?.invoked, true, `Page data bridge did not invoke ${handler}`);
}

function parseBridgePayload(payload, operation) {
  assert.equal(
    typeof payload,
    "string",
    `Page data bridge ${operation} returned a non-string payload`,
  );
  try {
    return JSON.parse(payload);
  } catch (error) {
    assert.fail(`Page data bridge ${operation} returned invalid JSON: ${String(error)}`);
  }
}
