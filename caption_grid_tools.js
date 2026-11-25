(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.CaptionGridTools = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const MIN_ZOOM = 1, MAX_ZOOM = 8, FULL_RES_ZOOM = 1.5;
  const THUMBNAIL_SIZES = [128, 192, 256, 384, 512, 768];

  function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }

  // Wheel deltas arrive in pixels (mice, touchpads, pinch) or lines (Firefox).
  function wheelPixels(deltaY, deltaMode = 0) {
    return deltaMode === 1 ? deltaY * 33 : deltaMode === 2 ? deltaY * 400 : deltaY;
  }

  // One mouse-wheel notch (100px) changes zoom by about 22%; pinch gestures
  // send small deltas and therefore zoom smoothly.
  function nextZoom(current, deltaY, deltaMode = 0) {
    const next = current * Math.exp(-wheelPixels(deltaY, deltaMode) * 0.002);
    return next < 1.02 ? MIN_ZOOM : clamp(next, MIN_ZOOM, MAX_ZOOM);
  }

  // Transform origin as fractions of the tile, so the point under the cursor
  // stays put while zooming and the view pans as the cursor moves.
  function pointerOrigin(rect, clientX, clientY) {
    if (!rect || !rect.width || !rect.height) return { x: 0.5, y: 0.5 };
    return {
      x: clamp((clientX - rect.left) / rect.width, 0, 1),
      y: clamp((clientY - rect.top) / rect.height, 0, 1),
    };
  }

  function wantsZoom(wheelMode, ctrlKey) {
    return wheelMode === 'wheel' ? true : Boolean(ctrlKey);
  }

  function needsFullResolution(zoom) { return zoom > FULL_RES_ZOOM; }

  function thumbnailSize(cssPixels, devicePixelRatio = 1) {
    const wanted = Math.ceil(cssPixels * Math.max(1, devicePixelRatio || 1));
    return THUMBNAIL_SIZES.find(size => size >= wanted) ?? THUMBNAIL_SIZES[THUMBNAIL_SIZES.length - 1];
  }

  // Groups map a name to a Set of image paths; an image may be in several groups.
  function toggleInGroup(groups, group, path) {
    const members = groups[group];
    if (!members) throw new Error(`Unknown selection group: ${group}`);
    if (members.has(path)) { members.delete(path); return false; }
    members.add(path); return true;
  }

  function pruneGroups(groups, existingPaths) {
    const existing = new Set(existingPaths);
    let removed = 0;
    for (const members of Object.values(groups)) {
      for (const path of [...members]) if (!existing.has(path)) { members.delete(path); removed++; }
    }
    return removed;
  }

  function parseProbability(value) {
    const text = String(value ?? '').trim();
    if (!text) return 1;
    const number = Number(text);
    return Number.isFinite(number) && number >= 0 && number <= 1 ? number : null;
  }

  return {
    MIN_ZOOM, MAX_ZOOM, FULL_RES_ZOOM, THUMBNAIL_SIZES,
    nextZoom, pointerOrigin, wantsZoom, needsFullResolution, thumbnailSize,
    toggleInGroup, pruneGroups, parseProbability, wheelPixels,
  };
});
