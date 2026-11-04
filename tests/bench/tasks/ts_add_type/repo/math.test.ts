import { test } from "node:test";
import assert from "node:assert";
import { add, sub } from "./math.ts";

test("add", () => {
    assert.strictEqual(add(2, 3), 5);
});

test("sub", () => {
    assert.strictEqual(sub(5, 3), 2);
});

test("sub negative", () => {
    assert.strictEqual(sub(2, 5), -3);
});