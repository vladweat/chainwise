import assert from "node:assert/strict";
import test from "node:test";

const polling = await import("../src/lib/status-polling.mjs");

test("transient status failures retry while missing or forbidden contracts stop", () => {
  assert.equal(polling.shouldRetryStatusFailure(undefined), true);
  assert.equal(polling.shouldRetryStatusFailure(503), true);
  assert.equal(polling.shouldRetryStatusFailure(408), true);
  assert.equal(polling.shouldRetryStatusFailure(429), true);
  assert.equal(polling.shouldRetryStatusFailure(401), false);
  assert.equal(polling.shouldRetryStatusFailure(403), false);
  assert.equal(polling.shouldRetryStatusFailure(404), false);
});

test("status retry delays grow exponentially and cap at 30 seconds", () => {
  assert.deepEqual(
    [1, 2, 3, 4, 5, 6].map(polling.getStatusRetryDelay),
    [2000, 4000, 8000, 16000, 30000, 30000],
  );
});
