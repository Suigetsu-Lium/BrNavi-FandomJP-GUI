"""Flexible extraction helpers for Backrooms route evidence."""
import re
import unicodedata
from urllib.parse import unquote, urlsplit

_LINK = re.compile(r"\[\[(https?://[^]|]+)(?:\|([^]]*))?\]\]")
_LEVEL = re.compile(
    r"(?<![A-Za-z0-9_])(?:Level\s+[-0-9A-Za-z.]+(?:\s*\u03b7)?|"
    r"The\s+(?:Frontrooms|\u03b7rooms|Void|Hub|End))(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_FRONTROOMS = re.compile(r"(?<![A-Za-z0-9_])(?:The\s+)?Frontrooms(?![\w])", re.IGNORECASE)
_UNCERTAIN = re.compile(r"\u4e0d\u78ba\u5b9a|\u672a\u78ba\u5b9a|\u53ef\u80fd\u6027|\u7a00|\u307e\u308c")
_CONFIRMED = re.compile(r"\u78ba\u5b9a")
_CUE = re.compile(
    r"\u5165\u308b\u3068|\u5165\u308b\u3053\u3068\u3067|\u901a\u308b\u3068|"
    r"\u901a\u904e\u3059\u308b\u3068|\u9032\u3080\u3068|\u79fb\u52d5\u3059\u308b\u3068|"
    r"\u5230\u9054\u3059\u308b\u3068|\u3064\u306a\u304c\u308b|\u7e4b\u304c\u308b|"
    r"\u8ee2\u9001\u3055\u308c|\u79fb\u52d5\u3059\u308b|\u884c\u304f|\u623b\u308b"
)


def clean_name(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = re.sub(r"(\d+)\u03b7", lambda match: match.group(1) + " " + chr(0x03b7), value)
    if value.casefold().startswith("level "):
        value = "Level " + value[6:]
    elif value.casefold().startswith("the "):
        value = "The " + value[4:]
    return value


def classify_route_certainty(section_name):
    if _UNCERTAIN.search(str(section_name or "")):
        return "Uncertain"
    if _CONFIRMED.search(str(section_name or "")):
        return "Confirmed"
    return "Unspecified"


def classify_route_node(name):
    value = unicodedata.normalize("NFKC", clean_name(name)).casefold()
    if "frontrooms" in value or "\u30d5\u30ed\u30f3\u30c8\u30eb\u30fc\u30e0" in value:
        return "Frontrooms"
    if value.startswith("level "):
        return "Level"
    if value in {"the void", "the hub", "the end", "the ?rooms"}:
        return "NamedLocation"
    return "Other"


def _url_target(url):
    path = urlsplit(str(url or "")).path
    if not path.startswith("/ja/wiki/"):
        return ""
    slug = unquote(path.split("/ja/wiki/", 1)[1]).replace("_", " ").strip()
    if slug.startswith("\u7ffb\u8a33/"):
        slug = slug.split("/", 1)[1]
    slug = re.sub(r"\s*\(\d+\)$", "", slug).strip()
    folded = slug.casefold()
    if folded.startswith("level "):
        return clean_name(slug)
    if folded in {"the frontrooms", "frontrooms", "the void", "the hub", "the end", "the ?rooms"}:
        return clean_name(slug)
    return ""


def extract_route_mentions(line):
    text = str(line or "")
    mentions, occupied = [], []
    for match in _LINK.finditer(text):
        target = _url_target(match.group(1))
        if not target and match.group(2):
            label_match = _LEVEL.search(match.group(2))
            if label_match:
                target = clean_name(label_match.group(0))
        if target:
            if target.casefold() == "the frontrooms":
                nearby = text[max(0, match.start() - 32):match.start()] + text[match.end():match.end() + 56]
                if re.search(r"\u3067\u4e00\u822c\u7684\u306b\u898b\u3089\u308c\u308b|\u3067\u898b\u3089\u308c\u308b|\u306b\u5b58\u5728\u3059\u308b|\u306b\u3042\u308b", nearby):
                    occupied.append((match.start(), match.end()))
                    continue
            mentions.append((target, match.start(), match.end(), "wiki-link"))
        occupied.append((match.start(), match.end()))
    for match in _LEVEL.finditer(text):
        if any(a <= match.start() < b for a, b in occupied):
            continue
        mentions.append((clean_name(match.group(0)), match.start(), match.end(), "text"))

    for match in _FRONTROOMS.finditer(text):
        before = text[max(0, match.start() - 32):match.start()]
        after = text[match.end():match.end() + 56]
        context = before + after
        if re.search(r"\u3067\u4e00\u822c\u7684\u306b\u898b\u3089\u308c\u308b|\u3067\u898b\u3089\u308c\u308b|\u306b\u5b58\u5728\u3059\u308b|\u306b\u3042\u308b", context):
            continue
        if re.search(r"(?:\u304b\u3089|\u3088\u308a)\s*$", before) or re.match(r"\s*(?:\u3078|\u306b)(?:\u79fb\u52d5|\u623b|\u5230\u9054|\u884c)", after):
            mentions.append(("The Frontrooms", match.start(), match.end(), "frontrooms"))

    result, seen = [], set()
    for item in sorted(mentions, key=lambda entry: entry[1]):
        key = item[0].casefold()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def extract_explicit_transitions(line):
    mentions = extract_route_mentions(line)
    if len(mentions) < 2 or not _CUE.search(str(line or "")):
        return []
    text = str(line or "")
    source_particles = tuple(chr(code) for code in (0x3067, 0x304b, 0x3089, 0x306b, 0x304a, 0x3044, 0x3066))
    destination_particles = tuple(chr(code) for code in (0x3078, 0x306b, 0x307e, 0x3067))
    pairs = []
    for index, (source, start, end, _kind) in enumerate(mentions[:-1]):
        next_target, next_start, next_end, _next_kind = mentions[index + 1]
        between = text[end:next_start].lstrip()
        after = text[next_end:next_end + 56]
        source_is_origin = between.startswith(source_particles)
        target_is_destination = (
            any(particle in between + after for particle in destination_particles)
            or any(cue in after for cue in ("??", "?", "??", "?"))
        )
        if source_is_origin and target_is_destination:
            pairs.append((source, next_target))
    return list(dict.fromkeys(pairs))
