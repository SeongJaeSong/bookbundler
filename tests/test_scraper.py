"""스크래퍼의 HTML 파싱과 요청 처리 단위 테스트 (네트워크 없음)."""
import httpx

from bookbundler import scraper
from bookbundler.scraper import (
    is_new_book_sold_out,
    parse_aladin_search,
    parse_yes24_search,
    yes24_search_book,
)

ALADIN_SEARCH_HTML = """
<div id="Search3_Result">
  <div class="ss_book_box" itemid="71213544">
    <div class="ss_book_list"><ul>
      <li>레터링 볼캡(공무원. 자격증. 토익. 전공서적 3만원 이상)</li>
      <li><a href="/shop/wproduct.aspx?ItemId=71213544"><b class="bo3">사용자 인터뷰</b></a></li>
      <li>
        <a href="/Search/wSearchResult.aspx?AuthorSearch=@123">스티브 포티걸</a> (지은이),
        <a href="/Search/wSearchResult.aspx?AuthorSearch=@456">김승권</a> (옮긴이) |
        <a href="/Search/wSearchResult.aspx?PublisherSearch=%c1%f6@7">지&amp;선(지앤선)</a> | 2015년 12월
      </li>
    </ul></div>
  </div>
  <div class="ss_book_box">
    <div class="ss_book_list"><ul><li>제목 없는 광고</li></ul></div>
  </div>
</div>
"""

YES24_SEARCH_HTML = """
<ul id="yesSchList">
  <li data-goods-no="61186169">
    <span class="gd_res">[도서]</span>
    <a class="gd_name" href="/product/goods/61186169">원칙 PRINCIPLES</a>
    <span class="authPub info_auth"><a href="#">레이 달리오</a> 저/<a href="#">고영태</a> 역</span>
    <span class="authPub info_pub"><a href="#">한빛비즈</a></span>
    <a href="https://www.yes24.com/Product/UsedShopHub/Hub/61186169">중고</a>
  </li>
  <li data-goods-no="133184817">
    <span class="gd_res">[eBook]</span>
    <a class="gd_name" href="/product/goods/133184817">나발 라비칸트의 부와 행복의 원칙</a>
    <a href="https://www.yes24.com/Product/UsedShopHub/Hub/133184817">중고</a>
  </li>
  <li data-goods-no="197279828">
    <span class="gd_res">[도서]</span>
    <a class="gd_name" href="/product/goods/197279828">중고 없는 신간</a>
  </li>
</ul>
"""


class TestAladinSearchParsing:
    def test_reads_authors_from_links_not_position(self):
        """사은품 안내 <li>가 앞에 끼어 있어도 저자와 출판사를 읽는다."""
        results = parse_aladin_search(ALADIN_SEARCH_HTML)
        assert len(results) == 1
        book = results[0]
        assert book.title == "사용자 인터뷰"
        assert book.item_id == "71213544"
        assert book.authors == ["스티브 포티걸", "김승권"]
        assert book.publisher == "지&선(지앤선)"


class TestYes24SearchParsing:
    def test_keeps_only_paper_books_with_used_listings(self):
        results = parse_yes24_search(YES24_SEARCH_HTML)
        assert [r.item_id for r in results] == ["61186169"]
        assert results[0].title == "원칙 PRINCIPLES"
        assert results[0].authors == ["레이 달리오", "고영태"]
        assert results[0].publisher == "한빛비즈"


class TestYes24FirstRequestRedirect:
    def test_retries_when_redirected_to_main(self, monkeypatch):
        """새 세션의 첫 검색이 메인으로 튕기면 한 번 더 요청한다."""
        monkeypatch.setattr(scraper, "REQUEST_DELAY", 0)
        calls = {"search": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/Product/Search":
                calls["search"] += 1
                if calls["search"] == 1:
                    return httpx.Response(
                        302, headers={"Location": "https://www.yes24.com/Main/default.aspx"},
                    )
                return httpx.Response(200, text=YES24_SEARCH_HTML)
            return httpx.Response(200, text="<html>메인</html>")

        client = httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=True,
        )
        results = yes24_search_book(client, "원칙")

        assert calls["search"] == 2
        assert [r.item_id for r in results] == ["61186169"]


class TestNewBookAvailability:
    def test_sold_out(self):
        text = "새상품 정가 20,000원 (품절) 새상품 상세보기 새상품 판매가 19,000원"
        assert is_new_book_sold_out(text)

    def test_out_of_print(self):
        text = "새상품 정가 29,000원 (절판) 새상품 판매가 26,100원"
        assert is_new_book_sold_out(text)

    def test_available(self):
        text = "새상품 정가 15,000원 새상품 상세보기 새상품 판매가 13,500원 + 마일리지 750원"
        assert not is_new_book_sold_out(text)


def _yes24_item(goods_id: str, title: str, author: str) -> str:
    return f"""
  <li data-goods-no="{goods_id}">
    <span class="gd_res">[도서]</span>
    <a class="gd_name" href="/product/goods/{goods_id}">{title}</a>
    <span class="authPub info_auth"><a href="#">{author}</a> 저</span>
    <a href="https://www.yes24.com/Product/UsedShopHub/Hub/{goods_id}">중고</a>
  </li>"""


class TestFindSameBookOnYes24:
    def test_retries_with_author_when_title_search_misses(self, monkeypatch):
        """제목만으로 같은 책이 안 나오면 저자를 붙여 다시 검색한다."""
        monkeypatch.setattr(scraper, "REQUEST_DELAY", 0)
        queries: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            query = request.url.params["query"]
            queries.append(query)
            if "로렌스" in query:
                body = _yes24_item("3527784", "문명전쟁", "로렌스 라이트")
            else:
                body = _yes24_item("236844", "제1차 문명전쟁", "마흐디 엘만즈라")
            return httpx.Response(200, text=f'<ul id="yesSchList">{body}</ul>')

        client = httpx.Client(transport=httpx.MockTransport(handler))
        match = scraper._find_same_book_on_yes24(client, "문명전쟁", "로렌스 라이트")

        assert queries == ["문명전쟁", "문명전쟁 로렌스 라이트"]
        assert match is not None
        assert match.candidate.item_id == "3527784"

    def test_rejects_different_book(self, monkeypatch):
        """비슷한 제목의 다른 책은 매물이 섞이지 않게 받아들이지 않는다."""
        monkeypatch.setattr(scraper, "REQUEST_DELAY", 0)

        def handler(request: httpx.Request) -> httpx.Response:
            body = _yes24_item("236844", "제1차 문명전쟁", "마흐디 엘만즈라")
            return httpx.Response(200, text=f'<ul id="yesSchList">{body}</ul>')

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert scraper._find_same_book_on_yes24(client, "문명전쟁", "로렌스 라이트") is None
