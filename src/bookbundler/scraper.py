from __future__ import annotations

import re
import time
from urllib.parse import quote, urlencode

import httpx
from bs4 import BeautifulSoup, Tag

from bookbundler.matching import (
    Candidate,
    Match,
    is_isbn,
    normalize_isbn,
    parse_query,
    pick_best,
)
from bookbundler.models import Book, Listing

# 알라딘 중고 검색 관련 상수
ALADIN_SEARCH_URL = "https://www.aladin.co.kr/search/wsearchresult.aspx"
ALADIN_USED_ITEM_URL = "https://www.aladin.co.kr/shop/UsedShop/wuseditemall.aspx"
ALADIN_DEFAULT_SHIPPING = 2500

# YES24 중고 검색 관련 상수
YES24_SEARCH_URL = "https://www.yes24.com/Product/Search"
YES24_USED_HUB_URL = "https://www.yes24.com/Product/UsedShopHub/Hub"
YES24_DEFAULT_SHIPPING = 3500

REQUEST_DELAY = 1.5  # 요청 간 대기 시간 (초)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def create_client() -> httpx.Client:
    """HTTP 클라이언트 생성."""
    return httpx.Client(
        headers=HEADERS,
        follow_redirects=True,
        timeout=30.0,
    )


def search_book(client: httpx.Client, query: str) -> list[Candidate]:
    """알라딘에서 책을 검색하여 후보 목록을 반환한다.

    중고 매장(UsedStore) 검색은 알라딘이 직접 가진 중고만 보여줘서
    판매자 중고만 있는 책을 놓치므로, 도서 검색으로 상품을 찾은 뒤
    상품 ID로 중고 매물을 모은다.
    """
    search_word = normalize_isbn(query) if is_isbn(query) else query
    params = {
        "SearchWord": search_word,
        "SearchTarget": "Book",
    }
    resp = client.get(ALADIN_SEARCH_URL, params=params)
    resp.raise_for_status()
    return parse_aladin_search(resp.text)


def parse_aladin_search(html: str) -> list[Candidate]:
    """알라딘 검색 결과 HTML에서 후보 목록을 뽑는다."""
    soup = BeautifulSoup(html, "html.parser")

    results: list[Candidate] = []
    items = soup.select("#Search3_Result .ss_book_box")
    if not items:
        items = soup.select(".ss_book_box")

    for item in items:
        # 제목: .bo3는 <b> 태그 (링크 아님)
        title_el = item.select_one(".bo3")
        if title_el is None:
            continue

        title = title_el.get_text(strip=True)

        # ItemId: div.ss_book_box의 itemid 속성 또는 링크에서 추출
        item_id = item.get("itemid")
        if item_id is None:
            for a in item.find_all("a", href=True):
                m = re.search(r"ItemId=(\d+)", str(a["href"]))
                if m:
                    item_id = m.group(1)
                    break

        if item_id is None:
            continue

        # 저자/출판사: 목록 앞쪽에 사은품 안내 <li>가 끼어들 때가 있어
        # 위치 대신 검색 링크로 찾는다
        authors = [
            a.get_text(strip=True)
            for a in item.select("a[href*='AuthorSearch']")
        ]
        publisher_el = item.select_one("a[href*='PublisherSearch']")
        publisher = publisher_el.get_text(strip=True) if publisher_el else ""

        results.append(Candidate(
            title=title,
            item_id=str(item_id),
            authors=authors,
            publisher=publisher,
        ))

    return results


def fetch_used_listings(
    client: httpx.Client,
    item_id: str,
    book_index: int,
    book: Book,
    max_pages: int = 5,
) -> list[Listing]:
    """특정 책의 중고 매물 목록을 스크래핑한다 (여러 페이지).

    Args:
        client: HTTP 클라이언트
        item_id: 알라딘 상품 ID
        book_index: 입력 책 목록에서의 인덱스
        book: 책 정보
        max_pages: 최대 스크래핑 페이지 수

    Returns:
        해당 책의 매물 리스트
    """
    listings: list[Listing] = []

    # TabType=1(판매자 중고) + TabType=3(중고매장/이 광활한 우주점) 모두 수집
    for tab_type in ["1", "3"]:
        soup = _fetch_aladin_tab(
            client, item_id, tab_type, book_index, book, listings, max_pages,
        )
        # 첫 탭의 첫 페이지에서 정가 + 새 책 판매가 추출
        if soup is not None and book.original_price is None:
            sidebar = soup.select_one(".Ere_prod_Binfowrap_used2")
            if sidebar:
                sidebar_text = sidebar.get_text()
                m = re.search(r"정가\s*([\d,]+)\s*원", sidebar_text)
                if m:
                    book.original_price = int(m.group(1).replace(",", ""))

                # 새 책 판매가를 가상 매물로 추가 (품절, 절판이면 살 수 없으므로 제외)
                m2 = re.search(r"판매가\s*([\d,]+)\s*원", sidebar_text)
                if m2 and not is_new_book_sold_out(sidebar_text):
                    new_price = int(m2.group(1).replace(",", ""))
                    listings.append(Listing(
                        book_index=book_index,
                        seller_id="aladin:new_book",
                        seller_name="알라딘 새 책",
                        price=new_price,
                        condition="새 책",
                        shipping_cost=ALADIN_DEFAULT_SHIPPING,
                        free_shipping_threshold=15000,
                    ))

    return listings


# 중고 상품 페이지 사이드바의 "정가 20,000원 (품절)" 같은 표기
_NEW_BOOK_UNAVAILABLE = re.compile(r"정가\s*[\d,]+\s*원\s*\([^)]*(?:품절|절판)[^)]*\)")


def is_new_book_sold_out(sidebar_text: str) -> bool:
    """알라딘 중고 페이지 사이드바에서 새 책이 품절이나 절판인지 판별한다."""
    return _NEW_BOOK_UNAVAILABLE.search(sidebar_text) is not None


def _fetch_aladin_tab(
    client: httpx.Client,
    item_id: str,
    tab_type: str,
    book_index: int,
    book: Book,
    listings: list[Listing],
    max_pages: int,
) -> BeautifulSoup | None:
    """알라딘 중고 매물의 특정 탭을 페이지별로 스크래핑한다. 첫 페이지 soup을 반환."""
    first_soup: BeautifulSoup | None = None
    for page in range(1, max_pages + 1):
        params = {
            "ItemId": item_id,
            "TabType": tab_type,
            "page": str(page),
        }
        resp = client.get(ALADIN_USED_ITEM_URL, params=params)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if first_soup is None:
            first_soup = soup

        table = soup.select_one(".Ere_usedsell_table")
        if table is None:
            break

        form = table.select_one("form")
        if form:
            rows = form.select("tr")
        else:
            rows = table.select("tbody tr")

        if not rows:
            break

        for row in rows:
            if not isinstance(row, Tag):
                continue
            listing = _parse_listing_row(row, book_index, book)
            if listing is not None:
                listings.append(listing)

        # 총 페이지 수 확인 — 더 이상 페이지가 없으면 중단
        pager = soup.select_one(".Ere_usedsell_num_box")
        if pager:
            total_pages_match = re.search(r"/\s*(\d+)\s*페이지", pager.get_text())
            if total_pages_match:
                total_pages = int(total_pages_match.group(1))
                if page >= total_pages:
                    break

        if page < max_pages:
            time.sleep(0.5)  # 페이지 간 짧은 대기

    return first_soup


def _parse_listing_row(row: Tag, book_index: int, book: Book) -> Listing | None:
    """매물 테이블의 한 행을 파싱한다."""
    # 가격 추출
    price_el = row.select_one(".price .Ere_sub_pink span.Ere_fs20")
    if price_el is None:
        price_el = row.select_one(".price span")
    if price_el is None:
        return None

    price_text = price_el.get_text(strip=True)
    price = _parse_price(price_text)
    if price is None or price <= 0:
        return None

    # 판매자 추출 — .seller div 안의 첫 번째 <a> (SC= 링크)
    seller_div = row.select_one(".seller")
    seller_el = None
    if seller_div:
        seller_el = seller_div.select_one("a[href*='SC=']")
        if seller_el is None:
            seller_el = seller_div.select_one("a[href*='wshopitem']")
        if seller_el is None:
            seller_el = seller_div.select_one("a")

    seller_name = seller_el.get_text(strip=True) if seller_el else "알수없음"

    # 판매자 ID (링크에서 추출)
    seller_id = seller_name  # 폴백
    if seller_el and seller_el.get("href"):
        sid_match = re.search(r"(?:SC|OffCode)=(\w+)", str(seller_el["href"]))
        if sid_match:
            seller_id = sid_match.group(1)

    # 상태 추출 — .Ere_sub_top 안에 .Ere_sub_middle 등의 span
    condition_el = row.select_one(".Ere_sub_top .Ere_sub_middle")
    if condition_el is None:
        condition_el = row.select_one(".Ere_sub_top span")
    if condition_el is None:
        condition_el = row.select_one(".Ere_sub_top")
    condition = condition_el.get_text(strip=True) if condition_el else "중"

    # 배송비 추출
    shipping_cost = ALADIN_DEFAULT_SHIPPING
    price_lis = row.select(".price li")
    for li in price_lis:
        li_text = li.get_text(strip=True)
        if "배송비" in li_text:
            if "무료" in li_text:
                shipping_cost = 0
            else:
                parsed = _parse_price(li_text)
                if parsed is not None:
                    shipping_cost = parsed
            break

    # 알라딘 직접배송 여부 (이 광활한 우주점, 중고매장XX점, 알라딘 중고 등)
    # 이 매물들은 모두 알라딘이 배송하며, 15,000원 이상 무료배송
    # seller href가 javascript:void(0)이거나 OffCode가 있으면 매장 매물
    seller_href = str(seller_el.get("href", "")) if seller_el else ""
    is_aladin_direct = (
        "우주점" in seller_name
        or "알라딘" in seller_name
        or "중고매장" in seller_name
        or "OffCode" in seller_href
        or (seller_href == "javascript:void(0);" and seller_name != "알수없음")
    )
    free_shipping_threshold: int | None = None
    if is_aladin_direct:
        seller_id = "aladin_direct"
        seller_name = f"{seller_name} (알라딘 직접배송)"
        free_shipping_threshold = 15000

    # 매물 URL
    url = ""
    link_el = row.select_one("a[href]")
    if link_el:
        href = link_el.get("href", "")
        if href and not str(href).startswith("javascript"):
            url = str(href)
            if url.startswith("/"):
                url = f"https://www.aladin.co.kr{url}"

    return Listing(
        book_index=book_index,
        seller_id=f"aladin:{seller_id}",
        seller_name=seller_name if is_aladin_direct else f"{seller_name} (알라딘)",
        price=price,
        condition=condition,
        shipping_cost=shipping_cost,
        free_shipping_threshold=free_shipping_threshold,
        is_aladin_direct=is_aladin_direct,
        url=url,
    )


# ── YES24 스크래핑 ──────────────────────────────────────────────


def yes24_search_book(client: httpx.Client, query: str) -> list[Candidate]:
    """YES24에서 중고 매물이 있는 책을 검색한다."""
    search_word = normalize_isbn(query) if is_isbn(query) else query
    params = {"domain": "ALL", "query": search_word}
    resp = client.get(YES24_SEARCH_URL, params=params)
    resp.raise_for_status()
    # 새 세션의 첫 검색은 쿠키를 심으며 메인 페이지로 보내므로 한 번 더 요청한다
    if not _is_yes24_search_page(resp):
        time.sleep(REQUEST_DELAY)
        resp = client.get(YES24_SEARCH_URL, params=params)
        resp.raise_for_status()
    return parse_yes24_search(resp.text)


def _is_yes24_search_page(resp: httpx.Response) -> bool:
    return "/product/search" in resp.url.path.lower()


def parse_yes24_search(html: str) -> list[Candidate]:
    """YES24 검색 결과 HTML에서 중고 매물이 있는 종이책 후보를 뽑는다."""
    soup = BeautifulSoup(html, "html.parser")

    results: list[Candidate] = []
    seen: set[str] = set()
    for item in soup.select("li[data-goods-no]"):
        category_el = item.select_one(".gd_res")
        if category_el and "eBook" in category_el.get_text():
            continue

        hub_el = item.select_one("a[href*='UsedShopHub/Hub/']")
        if hub_el is None:
            continue  # 중고 매물 없음
        m = re.search(r"Hub/(\d+)", str(hub_el["href"]))
        if m is None or m.group(1) in seen:
            continue
        goods_id = m.group(1)

        title_el = item.select_one(".gd_name")
        if title_el is None:
            continue

        auth_el = item.select_one(".info_auth")
        authors = (
            [a.get_text(strip=True) for a in auth_el.select("a")]
            if auth_el else []
        )
        if auth_el and not authors:
            authors = [auth_el.get_text(" ", strip=True)]
        publisher_el = item.select_one(".info_pub")

        seen.add(goods_id)
        results.append(Candidate(
            title=title_el.get_text(strip=True),
            item_id=goods_id,
            authors=authors,
            publisher=publisher_el.get_text(strip=True) if publisher_el else "",
        ))

    return results


def yes24_fetch_used_listings(
    client: httpx.Client,
    goods_id: str,
    book_index: int,
    book: Book,
    max_pages: int = 5,
) -> list[Listing]:
    """YES24에서 특정 책의 중고 매물을 스크래핑한다 (여러 페이지)."""
    listings: list[Listing] = []

    for page in range(1, max_pages + 1):
        resp = client.get(
            f"{YES24_USED_HUB_URL}/{goods_id}",
            params={"pageNo": str(page)},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        items = soup.select(".item_info")
        if not items:
            break

        for item in items:
            listing = _parse_yes24_item(item, book_index, book)
            if listing is not None:
                listings.append(listing)

        # 총 페이지 수 확인
        pager = soup.select_one(".pagenNum")
        if pager:
            m = re.search(r"(\d+)/(\d+)", pager.get_text())
            if m and page >= int(m.group(2)):
                break

        if page < max_pages:
            time.sleep(0.5)

    return listings


def _parse_yes24_item(item: Tag, book_index: int, book: Book) -> Listing | None:
    """YES24 매물 항목을 파싱한다."""
    # 가격
    price_el = item.select_one(".info_price .yes_b")
    if price_el is None:
        return None
    price = _parse_price(price_el.get_text(strip=True))
    if price is None or price <= 0:
        return None

    # 상태
    cond_el = item.select_one(".ico_used")
    condition = cond_el.get_text(strip=True) if cond_el else "중"

    # 배송비 + 판매자 + 무료배송 임계값 (배송 정보 텍스트에서 추출)
    # 패턴 예시:
    #   "배송비 : 6,000원, 곰돌이중고책방에서 직접 배송"
    #   "배송비 : 3,200원, 포레스토포레스포에서 25,000원 이상 구매 시 무료배송"
    shipping_cost = YES24_DEFAULT_SHIPPING
    free_shipping_threshold: int | None = None
    seller_name = "알수없음"
    deli_el = item.select_one(".info_deli")
    if deli_el:
        deli_text = deli_el.get_text(strip=True)
        # 기본 배송비는 항상 파싱
        m = re.search(r"배송비\s*:\s*([\d,]+)", deli_text)
        if m:
            shipping_cost = int(m.group(1).replace(",", ""))
        # 무료배송 임계값: "X원 이상 구매 시 무료배송"
        m = re.search(r"([\d,]+)원\s*이상\s*구매\s*시\s*무료", deli_text)
        if m:
            free_shipping_threshold = int(m.group(1).replace(",", ""))
        # 조건 없는 진짜 무료배송: "배송비" 자체가 없고 "무료배송"만 있는 경우
        elif "무료배송" in deli_text and "배송비" not in deli_text:
            shipping_cost = 0
        # 판매자: "원, OOO에서" 또는 "배송, OOO에서" 패턴
        m = re.search(r"(?:원,|배송,)\s*(.+?)에서", deli_text)
        if m:
            seller_name = m.group(1).strip()

    # 매물 URL + 상품 ID (판매자 식별에 사용)
    url = ""
    goods_id = ""
    name_el = item.select_one(".gd_name")
    if name_el and name_el.get("href"):
        href = str(name_el["href"])
        url = f"https://www.yes24.com{href}" if href.startswith("/") else href
        m = re.search(r"/goods/(\d+)", href)
        if m:
            goods_id = m.group(1)

    # 판매자 ID: 이름 기반 (YES24는 판매자 고유 ID가 URL에 없음)
    seller_id = re.sub(r"\s+", "", seller_name)  # 공백 제거

    return Listing(
        book_index=book_index,
        seller_id=f"yes24:{seller_id}",
        seller_name=f"{seller_name} (YES24)",
        price=price,
        condition=condition,
        shipping_cost=shipping_cost,
        free_shipping_threshold=free_shipping_threshold,
        url=url,
    )


def _parse_price(text: str) -> int | None:
    """가격 텍스트에서 숫자를 추출한다. 예: '3,500원' → 3500"""
    numbers = re.findall(r"[\d,]+", text)
    if not numbers:
        return None
    try:
        return int(numbers[0].replace(",", ""))
    except ValueError:
        return None


def scrape_books(
    queries: list[str],
    condition_filter: str | None = None,
    platforms: list[str] | None = None,
) -> tuple[list[Book], list[Listing]]:
    """여러 책을 검색하고 매물을 수집한다.

    Args:
        queries: 검색어 목록 (제목 또는 ISBN)
        condition_filter: 상태 필터 (상/중/하, None이면 전체)
        platforms: 검색할 플랫폼 목록 (["aladin", "yes24"], None이면 전부)

    Returns:
        (books, listings) 튜플
    """
    if platforms is None:
        platforms = ["aladin", "yes24"]

    books: list[Book] = []
    all_listings: list[Listing] = []

    with create_client() as client:
        for i, raw in enumerate(queries):
            query = parse_query(raw)
            by_isbn = is_isbn(query.text)
            book_listings: list[Listing] = []
            book: Book | None = None
            writer: str | None = None  # 알라딘에서 찾은 책의 대표 저자

            # ── 알라딘 ──
            if "aladin" in platforms:
                if i > 0:
                    time.sleep(REQUEST_DELAY)
                match = _choose(
                    search_book(client, query.text), query.text, query.author, by_isbn,
                )
                if match is not None:
                    book = _book_from_match(match, raw, query.author)
                    writer = next(iter(match.candidate.authors), None)
                    time.sleep(REQUEST_DELAY)
                    book_listings.extend(
                        fetch_used_listings(client, match.candidate.item_id, i, book)
                    )

            # ── YES24 ──
            if "yes24" in platforms:
                time.sleep(REQUEST_DELAY)
                if book is None:
                    match = _choose(
                        yes24_search_book(client, query.text),
                        query.text, query.author, by_isbn,
                    )
                    if match is not None:
                        book = _book_from_match(match, raw, query.author)
                else:
                    match = _find_same_book_on_yes24(client, book.title, writer)
                if match is not None:
                    time.sleep(REQUEST_DELAY)
                    book_listings.extend(
                        yes24_fetch_used_listings(
                            client, match.candidate.item_id, i, book,
                        )
                    )

            if book is None:
                book = Book(
                    title=query.text,
                    query=raw,
                    notes=["서점 검색에서 이 책을 찾지 못했습니다."],
                )
            books.append(book)

            # 상태 필터 적용
            if condition_filter:
                book_listings = [
                    lst for lst in book_listings
                    if condition_filter in lst.condition
                ]

            all_listings.extend(book_listings)

    return books, all_listings


def _choose(
    candidates: list[Candidate],
    title: str,
    author: str | None,
    by_isbn: bool,
) -> Match | None:
    """검색 결과에서 살 책을 고른다. ISBN 검색은 결과가 곧 그 책이다."""
    if by_isbn:
        return Match(candidate=candidates[0], confident=True) if candidates else None
    return pick_best(candidates, title, author)


def _find_same_book_on_yes24(
    client: httpx.Client,
    title: str,
    writer: str | None,
) -> Match | None:
    """알라딘에서 찾은 책과 같은 책을 YES24에서 찾는다.

    제목만으로는 비슷한 제목의 책에 밀려 결과 첫 페이지에 안 나올 때가 있어서
    ("문명전쟁"), 같은 책이 없으면 저자를 붙여 한 번 더 검색한다.
    """
    search_words = [title] + ([f"{title} {writer}"] if writer else [])
    for n, search_word in enumerate(search_words):
        if n > 0:
            time.sleep(REQUEST_DELAY)
        match = pick_best(yes24_search_book(client, search_word), title, writer)
        if _is_same_book(match):
            return match
    return None


def _is_same_book(match: Match | None) -> bool:
    """두 번째 서점의 검색 결과가 이미 찾은 책과 같은 책인지 확인한다.

    추정 결과나 저자가 다른 책을 받아들이면 다른 책의 매물이 섞이므로
    제목과 저자가 모두 맞을 때만 인정한다.
    """
    return (
        match is not None
        and match.confident
        and match.author_matched is not False
    )


def _book_from_match(match: Match, raw: str, author_hint: str | None) -> Book:
    """고른 검색 결과로 Book을 만들고, 사용자가 확인할 점을 남긴다."""
    cand = match.candidate
    book = Book(
        title=cand.title,
        author=", ".join(cand.authors) or None,
        publisher=cand.publisher or None,
        query=raw,
    )
    if not match.confident:
        book.notes.append(
            "입력한 제목과 정확히 맞는 책이 없어 가장 비슷한 책을 골랐습니다."
        )
    if match.author_matched is False:
        book.notes.append(f"저자가 입력한 이름({author_hint})과 다릅니다.")
    return book
