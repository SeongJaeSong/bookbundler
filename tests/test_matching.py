"""검색 결과에서 맞는 책을 고르는 로직 단위 테스트."""
from bookbundler.matching import (
    Candidate,
    author_matches,
    parse_query,
    pick_best,
    title_rank,
)


def _cand(title: str, item_id: str, *authors: str) -> Candidate:
    return Candidate(title=title, item_id=item_id, authors=list(authors))


class TestParseQuery:
    def test_title_only(self):
        q = parse_query("노인과 바다")
        assert q.text == "노인과 바다"
        assert q.author is None

    def test_title_with_author(self):
        q = parse_query("즉흥연기 - 키스 존스톤")
        assert q.text == "즉흥연기"
        assert q.author == "키스 존스톤"

    def test_hyphen_inside_word_is_not_separator(self):
        q = parse_query("9788937-460470")
        assert q.text == "9788937-460470"
        assert q.author is None


class TestTitleRank:
    def test_exact_ignores_spaces_and_tags(self):
        assert title_rank("사용자 인터뷰", "사용자인터뷰") == 4
        assert title_rank("설득의 심리학 1", "[예스리커버] 설득의 심리학 1") == 4

    def test_word_prefix_beats_plain_prefix(self):
        assert title_rank("원칙", "원칙 Principles") == 3
        assert title_rank("원칙", "원칙의 힘") == 2

    def test_contains_and_unrelated(self):
        assert title_rank("원칙", "결국 해내는 사람들의 원칙") == 1
        assert title_rank("원칙", "어쨌든, 클로드 코드") == 0


class TestAuthorMatches:
    def test_spelling_variant(self):
        assert author_matches("데이비드 알렌", ["데이비드 앨런", "김경섭"])

    def test_partial_name(self):
        assert author_matches("포티걸", ["스티브 포티걸", "김승권"])

    def test_different_person(self):
        assert not author_matches("데이비드 알렌", ["레이 달리오", "고영태"])


class TestPickBest:
    def test_skips_promoted_first_result(self):
        """광고성 첫 결과 대신 제목이 맞는 책을 고른다 (실제 '원칙' 검색 결과)."""
        candidates = [
            _cand("어쨌든, 클로드 코드", "402541634", "니시미 마사히로"),
            _cand("원칙 Principles", "147945799", "레이 달리오", "고영태"),
            _cand("주식투자 절대 원칙", "281664747", "박영옥"),
        ]
        match = pick_best(candidates, "원칙")
        assert match is not None
        assert match.candidate.item_id == "147945799"
        assert match.confident

    def test_exact_title_beats_earlier_similar_title(self):
        candidates = [
            _cand("유저 인터뷰 교과서", "313758891", "오쿠이즈미 나오코"),
            _cand("사용자 인터뷰", "71213544", "스티브 포티걸"),
        ]
        match = pick_best(candidates, "사용자 인터뷰")
        assert match is not None
        assert match.candidate.item_id == "71213544"

    def test_author_breaks_tie_between_same_titles(self):
        candidates = [
            _cand("즉흥연기", "2747463", "필립 베르나르디"),
            _cand("즉흥연기", "249938", "키스 존스톤", "이민아"),
        ]
        match = pick_best(candidates, "즉흥연기", "키스 존스톤")
        assert match is not None
        assert match.candidate.item_id == "249938"
        assert match.author_matched is True

    def test_wrong_author_still_picks_title_but_flags_it(self):
        candidates = [_cand("원칙 Principles", "147945799", "레이 달리오")]
        match = pick_best(candidates, "원칙", "데이비드 알렌")
        assert match is not None
        assert match.confident
        assert match.author_matched is False

    def test_misremembered_title_is_guessed_with_low_confidence(self):
        candidates = [
            _cand("끝도 없는 일 깔끔하게 해치우기", "332799", "데이비드 알렌"),
        ]
        match = pick_best(candidates, "끝도 없는 일 끝내기")
        assert match is not None
        assert not match.confident

    def test_contains_only_is_low_confidence(self):
        candidates = [_cand("결국 해내는 사람들의 원칙", "258108880", "앨런 피즈")]
        match = pick_best(candidates, "원칙")
        assert match is not None
        assert not match.confident

    def test_unrelated_results_return_none(self):
        candidates = [_cand("세계사신문 1", "79696", "세계사신문편찬위원회")]
        assert pick_best(candidates, "문명전쟁") is None

    def test_empty_candidates(self):
        assert pick_best([], "데미안") is None
