'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  parseDirectionQuery,
  containsDirection,
  editDescription,
} = require('./caption_direction_tools.js');

test('recognizes only the six scoped direction queries', () => {
  assert.deepEqual(parseDirectionQuery('  Her   LEFT '), { scope: 'her', side: 'left' });
  assert.deepEqual(parseDirectionQuery('his right'), { scope: 'his', side: 'right' });
  assert.deepEqual(parseDirectionQuery('their left'), { scope: 'their', side: 'left' });
  for (const query of ['the left', 'left', 'her left arm', 'her', '']) {
    assert.equal(parseDirectionQuery(query), null);
  }
});

test('swap changes both directions for only the searched pronoun', () => {
  const description = 'Her left arm is raised beside HER RIGHT hand. His left foot is near the left wall.';
  assert.equal(
    editDescription(description, 'her left', 'swap'),
    'Her right arm is raised beside HER LEFT hand. His left foot is near the left wall.',
  );
});

test('change replaces only the searched scoped phrase and direction', () => {
  const description = 'Her left arm is raised; her right hand rests by his left leg and the left wall.';
  assert.equal(
    editDescription(description, 'her left', 'change'),
    'Her right arm is raised; her right hand rests by his left leg and the left wall.',
  );
  assert.equal(containsDirection(description, 'their left'), false);
});

test('remove drops only sentences containing the full searched phrase', () => {
  const description = 'She lifts her left arm. The left wall stays visible! He checks her right hand? Their left feet are planted.';
  assert.equal(
    editDescription(description, 'her left', 'remove'),
    'The left wall stays visible! He checks her right hand? Their left feet are planted.',
  );
});

test('invalid queries and absent phrases leave the caption unchanged', () => {
  const description = 'The left hand is visible. His right leg is bent.';
  assert.equal(editDescription(description, 'the left', 'swap'), description);
  assert.equal(editDescription(description, 'her left', 'remove'), description);
});
