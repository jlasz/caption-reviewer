(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.CaptionFilterTools = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function flattenCaptionPart(value) {
    return String(value ?? '').replace(/\s+/g, ' ').trim();
  }

  function splitTags(value) {
    return String(value ?? '').split(',').map(tag => tag.trim()).filter(Boolean);
  }

  // Support inline tags plus prose as well as legacy blank-line captions.
  // proseOnly: the whole file is description (prose-only .caption training captions).
  function splitCaptionText(value, proseOnly = false) {
    value = String(value ?? '').trim();
    if (!value) return { tags: [], description: '' };
    if (proseOnly) return { tags: [], description: flattenCaptionPart(value) };
    const legacy = value.match(/\n\s*\n/);
    if (legacy) {
      const at = legacy.index;
      return {
        tags: splitTags(flattenCaptionPart(value.slice(0, at))),
        description: flattenCaptionPart(value.slice(at + legacy[0].length)),
      };
    }
    value = flattenCaptionPart(value);
    const boundary = value.match(/\.\s+(?=[A-Z0-9])/);
    if (boundary && value.slice(0, boundary.index).includes(',')) {
      return {
        tags: splitTags(value.slice(0, boundary.index)),
        description: value.slice(boundary.index + boundary[0].length).trim(),
      };
    }
    return { tags: splitTags(value), description: '' };
  }

  function joinCaptionText(tags, description) {
    const tagsText = tags.join(', '), prose = flattenCaptionPart(description);
    return tagsText && prose ? `${tagsText}. ${prose}` : tagsText || prose;
  }

  function searchableCaptionText(value, mode, parts = null) {
    if (mode === 'everything') return String(value ?? '');
    parts ??= splitCaptionText(value);
    return mode === 'caption' ? parts.description : parts.tags.join(', ');
  }

  return { flattenCaptionPart, splitTags, splitCaptionText, joinCaptionText, searchableCaptionText };
});
