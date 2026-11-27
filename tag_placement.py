"""Tag placement rules for Grid mode's "add tags": activation and @artist tags stay at the
front, duplicates are skipped, and ordinary tags can be added with a probability and at a
random position. The dataset tool manual.py uses the same functions, so both place tags
identically. Standard library only."""

from __future__ import annotations

import random
import re
import unicodedata

ACTIVATION_TAG_RE = re.compile(r"^brf[a-z0-9_-]+$", re.IGNORECASE)
ARTIST_TAG_PREFIX = "@"
NON_ARTIST_AT_TAGS = {"@_@"}


def is_activation_tag(tag: str) -> bool:
    stripped = tag.strip()
    return (
        tag == stripped
        and not stripped.startswith(ARTIST_TAG_PREFIX)
        and ACTIVATION_TAG_RE.fullmatch(stripped) is not None
    )


def leading_activation_tag_count(tags: list[str]) -> int:
    count = 0
    for tag in tags:
        if not is_activation_tag(tag):
            break
        count += 1
    return count


def is_artist_tag(tag: str) -> bool:
    stripped = tag.strip()
    return stripped.startswith(ARTIST_TAG_PREFIX) and stripped not in NON_ARTIST_AT_TAGS


# Symbols that are useful as intentional parts of artist handles/names even
# when they appear at the outer edge. Paired punctuation categories Ps/Pe cover
# parentheses, square/curly brackets, and Japanese brackets such as 「」『』【】.
PRESERVED_HANDLE_EDGE_CHARACTERS = frozenset(
    "!！?？:：;；#＃~～〜|｜^＾-_－&＆@＠"
)


def is_preserved_artist_edge_character(char: str) -> bool:
    """Return whether an outer symbol should remain part of an artist name."""
    category = unicodedata.category(char)
    return (
        category in {"Ps", "Pe", "Sm", "Pc"}
        or char in PRESERVED_HANDLE_EDGE_CHARACTERS
    )


def is_special_edge_character(char: str) -> bool:
    return unicodedata.category(char)[0] in {"P", "S"}


def strip_outer_special_symbols(value: str) -> str:
    """Strip outer punctuation/symbols except approved artist-name characters.

    This keeps brackets, mathematical/connector symbols, !, ?, :, ;, #, ~,
    pipes, carets, and common handle characters. Other edge punctuation and
    symbols, such as quotes, periods, commas, slashes, and decorative marks,
    are removed.
    """
    start = 0
    end = len(value)

    while start < end:
        char = value[start]
        if char.isspace():
            start += 1
            continue
        if is_special_edge_character(char) and not is_preserved_artist_edge_character(char):
            start += 1
            continue
        break

    while end > start:
        char = value[end - 1]
        if char.isspace():
            end -= 1
            continue
        if is_special_edge_character(char) and not is_preserved_artist_edge_character(char):
            end -= 1
            continue
        break

    return value[start:end]


def artist_tag_key(tag: str) -> str:
    tag = tag.strip()
    if tag.startswith(ARTIST_TAG_PREFIX):
        tag = tag[len(ARTIST_TAG_PREFIX) :]
    return " ".join(strip_outer_special_symbols(tag).strip().lower().split())


def normalize_artist_tag(tag: str) -> str:
    stripped = tag.strip()
    if not is_artist_tag(stripped):
        return stripped

    artist_name = artist_tag_key(stripped)
    if not artist_name:
        return stripped
    return ARTIST_TAG_PREFIX + artist_name


def normalize_caption_tag(tag: str) -> str:
    if is_artist_tag(tag):
        return normalize_artist_tag(tag)
    return tag


def leading_artist_tag_count(tags: list[str], start: int = 0) -> int:
    count = 0
    for tag in tags[start:]:
        if not is_artist_tag(tag):
            break
        count += 1
    return count


def merge_artist_tags_after_activation(
    existing_tags: list[str],
    artist_tags: list[str],
    *,
    replace_existing: bool = False,
    max_artists: int | None = 1,
) -> list[str]:
    """Place @artist tags directly after the leading activation tag block.

    By default captions are normalized to one artist/style tag. Pass
    replace_existing=True when an incoming artist should replace any old artist.
    """
    activation_count = leading_activation_tag_count(existing_tags)
    activation_tags = existing_tags[:activation_count]
    remaining_tags = existing_tags[activation_count:]
    incoming_artists = [normalize_artist_tag(tag) for tag in artist_tags if is_artist_tag(tag)]

    artists: list[str] = []
    artist_indexes: dict[str, int] = {}
    body_tags: list[str] = []

    def add_artist(tag: str) -> None:
        normalized_tag = normalize_artist_tag(tag)
        key = artist_tag_key(normalized_tag)
        if not key:
            return
        if key in artist_indexes:
            artists[artist_indexes[key]] = normalized_tag
            return
        artist_indexes[key] = len(artists)
        artists.append(normalized_tag)

    for tag in remaining_tags:
        if is_artist_tag(tag):
            add_artist(tag)
        else:
            body_tags.append(tag)

    if replace_existing and incoming_artists:
        artists = []
        artist_indexes = {}

    for tag in incoming_artists:
        add_artist(tag)

    if max_artists is not None and max_artists > 0:
        artists = artists[:max_artists]

    artist_keys = {artist_tag_key(tag) for tag in artists}
    body_tags = [tag for tag in body_tags if artist_tag_key(tag) not in artist_keys]

    return activation_tags + artists + body_tags


def ordinary_tag_start(tags: list[str]) -> int:
    """Return the first safe slot for an ordinary tag.

    Artist tags are normally directly after the activation block, but older or
    manually edited captions may have extra protected tags before the artist.
    Keeping the whole prefix through the last artist prevents randomized tags
    from being inserted ahead of either the activation or artist metadata.
    """
    activation_count = leading_activation_tag_count(tags)
    artist_indexes = [index for index, tag in enumerate(tags) if is_artist_tag(tag)]
    return max(activation_count, artist_indexes[-1] + 1 if artist_indexes else 0)


def partition_tags_for_caption(
    tags: list[str],
    probability: float,
    rng: random.Random | None = None,
) -> tuple[list[str], list[str]]:
    """Partition tags into independently selected and skipped lists."""
    if not 0.0 <= probability <= 1.0:
        raise ValueError("Tag probability must be between 0.0 and 1.0.")

    if probability == 0.0:
        return [], list(tags)
    if probability == 1.0:
        return list(tags), []

    random_source = rng or random
    selected: list[str] = []
    skipped: list[str] = []
    for tag in tags:
        destination = selected if random_source.random() < probability else skipped
        destination.append(tag)
    return selected, skipped


def append_tags_to_tag_list(
    existing: list[str],
    tags_to_add: list[str],
    *,
    randomize_placement: bool = False,
    important_placement: bool = False,
    rng: random.Random | None = None,
) -> tuple[list[str], bool]:
    """Return ``existing`` with ``tags_to_add`` merged in, plus whether it changed.

    In-memory core of append_tags_to_caption, also used by Caption Reviewer's
    grid mode so both tools place and deduplicate tags identically.
    """
    existing = list(existing)
    artist_tags_to_add = [tag for tag in tags_to_add if is_artist_tag(tag)]
    other_tags_to_add = [tag for tag in tags_to_add if not is_artist_tag(tag)]
    random_source = rng or random
    changed = False

    if artist_tags_to_add:
        merged = merge_artist_tags_after_activation(
            existing,
            artist_tags_to_add,
            replace_existing=True,
            max_artists=1,
        )
        if merged != existing:
            existing = merged
            changed = True

    seen = {tag.lower() for tag in existing}

    for tag in other_tags_to_add:
        key = tag.lower()
        if key not in seen:
            if is_activation_tag(tag):
                existing.insert(leading_activation_tag_count(existing), tag)
            elif is_artist_tag(tag):
                activation_count = leading_activation_tag_count(existing)
                artist_count = leading_artist_tag_count(existing, start=activation_count)
                existing.insert(activation_count + artist_count, tag)
            elif randomize_placement:
                body_start = ordinary_tag_start(existing)
                possible_indexes = range(body_start, len(existing) + 1)
                if important_placement:
                    slot_count = len(existing) - body_start + 1
                    weights = range(slot_count, 0, -1)
                    insertion_index = random_source.choices(
                        possible_indexes,
                        weights=weights,
                        k=1,
                    )[0]
                else:
                    insertion_index = random_source.randrange(body_start, len(existing) + 1)
                existing.insert(insertion_index, tag)
            else:
                existing.append(tag)
            seen.add(key)
            changed = True

    return existing, changed
