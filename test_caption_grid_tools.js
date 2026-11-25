'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  nextZoom, pointerOrigin, wantsZoom, needsFullResolution, thumbnailSize,
  toggleInGroup, pruneGroups, parseProbability, MAX_ZOOM,
} = require('./caption_grid_tools.js');

test('wheel zoom steps smoothly, snaps back to fit, and stays within 1x-8x', () => {
  const zoomedIn = nextZoom(1, -100);
  assert.ok(zoomedIn > 1.2 && zoomedIn < 1.25);
  assert.equal(nextZoom(zoomedIn, 100), 1);
  assert.equal(nextZoom(1, 500), 1);
  let zoom = 1;
  for (let step = 0; step < 50; step++) zoom = nextZoom(zoom, -100);
  assert.equal(zoom, MAX_ZOOM);
  assert.ok(Math.abs(nextZoom(1, -3, 1) - nextZoom(1, -99)) < 1e-9, 'line deltas count as ~33px');
  assert.ok(nextZoom(2, -4) > 2 && nextZoom(2, -4) < 2.05, 'pinch-sized deltas zoom gently');
});

test('zoom origin follows the cursor inside the tile and is clamped', () => {
  const rect = { left: 100, top: 50, width: 200, height: 100 };
  assert.deepEqual(pointerOrigin(rect, 150, 75), { x: 0.25, y: 0.25 });
  assert.deepEqual(pointerOrigin(rect, 0, 500), { x: 0, y: 1 });
  assert.deepEqual(pointerOrigin({ width: 0, height: 0 }, 1, 1), { x: 0.5, y: 0.5 });
});

test('wheel mode decides whether the wheel zooms or scrolls', () => {
  assert.equal(wantsZoom('ctrl', false), false);
  assert.equal(wantsZoom('ctrl', true), true);
  assert.equal(wantsZoom('wheel', false), true);
  assert.equal(wantsZoom('wheel', true), true);
  assert.equal(needsFullResolution(1.4), false);
  assert.equal(needsFullResolution(1.6), true);
});

test('thumbnail sizes are bucketed for the tile size and screen density', () => {
  assert.equal(thumbnailSize(200, 1), 256);
  assert.equal(thumbnailSize(200, 2), 512);
  assert.equal(thumbnailSize(120, 1), 128);
  assert.equal(thumbnailSize(520, 2), 768);
});

test('two selection groups are independent and pruned after rescans', () => {
  const groups = { a: new Set(), b: new Set() };
  assert.equal(toggleInGroup(groups, 'a', 'x.png'), true);
  assert.equal(toggleInGroup(groups, 'b', 'x.png'), true);
  assert.equal(toggleInGroup(groups, 'a', 'y.png'), true);
  assert.equal(toggleInGroup(groups, 'a', 'x.png'), false);
  assert.deepEqual([...groups.a], ['y.png']);
  assert.deepEqual([...groups.b], ['x.png']);
  assert.equal(pruneGroups(groups, ['x.png']), 1);
  assert.deepEqual([...groups.a], []);
  assert.throws(() => toggleInGroup(groups, 'c', 'x.png'));
});

test('chance accepts manual.py-style probabilities only', () => {
  assert.equal(parseProbability(''), 1);
  assert.equal(parseProbability('0.25'), 0.25);
  assert.equal(parseProbability('0'), 0);
  assert.equal(parseProbability('1.5'), null);
  assert.equal(parseProbability('abc'), null);
});
