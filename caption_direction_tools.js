(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.CaptionDirectionTools = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const QUERY_RE = /^(her|his|their)\s+(left|right)$/iu;

  function parseDirectionQuery(value) {
    const match = String(value ?? '').trim().match(QUERY_RE);
    if (!match) return null;
    return { scope: match[1].toLocaleLowerCase(), side: match[2].toLocaleLowerCase() };
  }

  function opposite(side) {
    return side === 'left' ? 'right' : 'left';
  }

  function copyCase(source, replacement) {
    if (source === source.toLocaleUpperCase()) return replacement.toLocaleUpperCase();
    if (source[0] === source[0].toLocaleUpperCase()) {
      return replacement[0].toLocaleUpperCase() + replacement.slice(1);
    }
    return replacement;
  }

  function phraseRegex(query, bothSides = false, global = false) {
    const side = bothSides ? '(left|right)' : `(${query.side})`;
    return new RegExp(`\\b(${query.scope})(\\s+)${side}\\b`, global ? 'giu' : 'iu');
  }

  function containsDirection(description, value, bothSides = false) {
    const query = typeof value === 'string' ? parseDirectionQuery(value) : value;
    return Boolean(query && phraseRegex(query, bothSides).test(description));
  }

  function replaceDirections(description, query, bothSides) {
    return description.replace(phraseRegex(query, bothSides, true), (match, scope, spacing, side) => {
      return scope + spacing + copyCase(side, opposite(side.toLocaleLowerCase()));
    });
  }

  function sentenceSegments(description) {
    if (typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function') {
      const segmenter = new Intl.Segmenter(undefined, { granularity: 'sentence' });
      return Array.from(segmenter.segment(description), part => part.segment);
    }
    const segments = [];
    const boundary = /[.!?]+(?=\s|$)/g;
    let start = 0;
    for (const match of description.matchAll(boundary)) {
      let end = match.index + match[0].length;
      while (end < description.length && /\s/.test(description[end])) end++;
      segments.push(description.slice(start, end));
      start = end;
    }
    if (start < description.length) segments.push(description.slice(start));
    return segments;
  }

  function removeMatchingSentences(description, query) {
    const kept = sentenceSegments(description).filter(segment => !containsDirection(segment, query));
    return kept.join('').replace(/\s+/g, ' ').trim();
  }

  function editDescription(description, value, action) {
    const query = typeof value === 'string' ? parseDirectionQuery(value) : value;
    if (!query || !containsDirection(description, query, action === 'swap')) return description;
    if (action === 'swap') return replaceDirections(description, query, true);
    if (action === 'change') return replaceDirections(description, query, false);
    if (action === 'remove') return removeMatchingSentences(description, query);
    return description;
  }

  return { parseDirectionQuery, containsDirection, editDescription, opposite };
});
