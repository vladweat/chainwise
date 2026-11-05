const assert = require("node:assert/strict");
const test = require("node:test");

const {
  isCompletedStatus,
  isIndexingStatus,
  isReadyStatus,
  isTerminalStatus,
} = require("../build/test-lib/contract-status.js");

test("ready and loaded are completed, while failed is not", () => {
  assert.equal(isCompletedStatus("ready"), true);
  assert.equal(isCompletedStatus("loaded"), true);
  assert.equal(isCompletedStatus("failed"), false);
});

test("ready, loaded, and failed are terminal statuses", () => {
  assert.equal(isTerminalStatus("ready"), true);
  assert.equal(isTerminalStatus("loaded"), true);
  assert.equal(isTerminalStatus("failed"), true);
  assert.equal(isTerminalStatus("indexing"), false);
});

test("pending and indexing statuses remain in progress", () => {
  assert.equal(isIndexingStatus("pending"), true);
  assert.equal(isIndexingStatus("indexing"), true);
  assert.equal(isIndexingStatus("failed"), false);
});

test("only ready status enables the ready-only state", () => {
  assert.equal(isReadyStatus("ready"), true);
  assert.equal(isReadyStatus("loaded"), false);
  assert.equal(isReadyStatus("pending"), false);
});
