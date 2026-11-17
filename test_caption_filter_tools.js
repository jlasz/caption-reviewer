'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { splitCaptionText, searchableCaptionText } = require('./caption_filter_tools.js');

test('splits current and legacy caption formats into tags and caption prose', () => {
  assert.deepEqual(
    splitCaptionText('dark skin, blue eyes. A pale figure stands by the window.'),
    { tags: ['dark skin', 'blue eyes'], description: 'A pale figure stands by the window.' },
  );
  assert.deepEqual(
    splitCaptionText('dark skin, blue eyes\n\nA pale figure stands by the window.'),
    { tags: ['dark skin', 'blue eyes'], description: 'A pale figure stands by the window.' },
  );
});

test('Everything, Caption, and Tags expose only their intended search scope', () => {
  const text = 'dark skin, blue eyes. A pale figure wears a dark coat.';
  const parts = splitCaptionText(text);

  assert.match(searchableCaptionText(text, 'everything', parts), /dark skin/);
  assert.doesNotMatch(searchableCaptionText(text, 'caption', parts), /dark skin/);
  assert.match(searchableCaptionText(text, 'tags', parts), /dark skin/);

  assert.match(searchableCaptionText(text, 'everything', parts), /pale figure/);
  assert.match(searchableCaptionText(text, 'caption', parts), /pale figure/);
  assert.doesNotMatch(searchableCaptionText(text, 'tags', parts), /pale figure/);
});

test('Caption-scoped exclusion ignores a match that exists only as a tag', () => {
  const text = 'dark skin, blue eyes. A pale figure stands by the window.';
  const excludedFromCaption = searchableCaptionText(text, 'caption').toLocaleLowerCase();
  const excludedFromTags = searchableCaptionText(text, 'tags').toLocaleLowerCase();

  assert.equal(excludedFromCaption.includes('dark skin'), false);
  assert.equal(excludedFromTags.includes('dark skin'), true);
});
