from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# "제목 - 저자" 형식으로 저자를 함께 적을 때 쓰는 구분자
AUTHOR_SEPARATOR = " - "

# 제목이 정확히 맞는 후보가 없을 때, 이 이상 비슷하면 추정 결과로 채택
FUZZY_TITLE_THRESHOLD = 0.5
# 표기가 조금 다른 저자명(알렌/앨런 등)을 같은 사람으로 보는 기준
AUTHOR_SIMILARITY_THRESHOLD = 0.6

# 제목 앞에 붙는 [예스리커버], [중고] 같은 표시
_LEADING_TAG = re.compile(r"^\s*\[[^\]]*\]\s*")
_WORD = re.compile(r"[0-9a-z가-힣]+")


@dataclass
class BookQuery:
    """사용자가 입력한 책 하나."""

    text: str  # 제목 또는 ISBN
    author: str | None = None


@dataclass
class Candidate:
    """서점 검색 결과의 책 하나."""

    title: str
    item_id: str
    authors: list[str] = field(default_factory=list)
    publisher: str = ""


@dataclass
class Match:
    """검색 결과에서 고른 책."""

    candidate: Candidate
    confident: bool  # False면 제목이 맞지 않아 비슷한 책으로 추정한 것
    author_matched: bool | None = None  # 비교할 저자가 없으면 None


def is_isbn(text: str) -> bool:
    """ISBN(10자리 또는 13자리 숫자)인지 판별한다."""
    digits = text.replace("-", "").strip()
    return digits.isdigit() and len(digits) in (10, 13)


def normalize_isbn(text: str) -> str:
    """ISBN에서 하이픈 등을 제거한다."""
    return text.replace("-", "").strip()


def parse_query(raw: str) -> BookQuery:
    """'제목 - 저자' 형식이면 제목과 저자로 나눈다."""
    text = raw.strip()
    if AUTHOR_SEPARATOR in text:
        title, author = text.rsplit(AUTHOR_SEPARATOR, 1)
        if title.strip() and author.strip():
            return BookQuery(text=title.strip(), author=author.strip())
    return BookQuery(text=text)


def _words(text: str) -> list[str]:
    return _WORD.findall(_LEADING_TAG.sub("", text).lower())


def normalize(text: str) -> str:
    """비교용으로 공백, 문장부호, 앞머리 표시를 지운다."""
    return "".join(_words(text))


def title_rank(query: str, title: str) -> int:
    """검색어와 제목이 얼마나 맞는지 등급을 매긴다 (클수록 정확)."""
    q, t = normalize(query), normalize(title)
    if not q or not t:
        return 0
    if q == t:
        return 4
    query_words = _words(query)
    if _words(title)[: len(query_words)] == query_words:
        return 3  # "원칙" → "원칙 Principles"
    if t.startswith(q) or q.startswith(t):
        return 2
    if q in t:
        return 1
    return 0


def author_matches(hint: str, authors: list[str]) -> bool:
    """저자 힌트가 후보의 저자 중 한 명과 같은 사람인지 판별한다."""
    h = normalize(hint)
    if not h:
        return False
    for name in authors:
        n = normalize(name)
        if not n:
            continue
        if h in n or n in h:
            return True
        if SequenceMatcher(None, h, n).ratio() >= AUTHOR_SIMILARITY_THRESHOLD:
            return True
    return False


def pick_best(
    candidates: list[Candidate],
    title: str,
    author: str | None = None,
) -> Match | None:
    """제목과 저자를 기준으로 검색 결과에서 가장 맞는 책을 고른다.

    서점 검색은 광고나 비슷한 제목의 책을 먼저 보여줄 때가 많아서
    첫 번째 결과를 그대로 쓰면 엉뚱한 책을 사게 된다.
    제목이 맞는 후보가 없으면 가장 비슷한 제목을 추정 결과로 돌려주고,
    그마저 없으면 None을 돌려준다.
    """
    if not candidates:
        return None

    scored = []
    for position, cand in enumerate(candidates):
        rank = title_rank(title, cand.title)
        matched = author_matches(author, cand.authors) if author else None
        scored.append((rank, bool(matched), position, cand, matched))

    # 제목이 맞으면서 저자도 맞는 후보 > 제목 등급 > 저자 > 검색 순서
    best = max(scored, key=lambda s: (s[0] > 0 and s[1], s[0], s[1], -s[2]))
    rank, _, _, cand, matched = best
    if rank >= 2:
        return Match(candidate=cand, confident=True, author_matched=matched)
    if rank == 1:
        return Match(candidate=cand, confident=False, author_matched=matched)

    # 제목이 하나도 맞지 않으면 가장 비슷한 제목으로 추정
    target = normalize(title)

    def similarity(cand: Candidate) -> float:
        return SequenceMatcher(None, target, normalize(cand.title)).ratio()

    fuzzy = max(
        scored,
        key=lambda s: (s[1], similarity(s[3]), -s[2]),
    )
    _, author_ok, _, cand, matched = fuzzy
    if author_ok or similarity(cand) >= FUZZY_TITLE_THRESHOLD:
        return Match(candidate=cand, confident=False, author_matched=matched)
    return None
