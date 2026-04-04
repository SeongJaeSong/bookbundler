from __future__ import annotations

from pulp import (
    PULP_CBC_CMD,
    LpBinary,
    LpContinuous,
    LpMinimize,
    LpProblem,
    LpStatus,
    LpVariable,
    lpSum,
    value,
)

from bookbundler.models import (
    Assignment,
    Book,
    Listing,
    OptimizationResult,
)


def optimize(
    books: list[Book],
    listings: list[Listing],
) -> OptimizationResult:
    """배송비 포함 총비용을 최소화하는 판매자 조합을 찾는다.

    무료배송 임계값을 고려한다: 해당 판매자에서의 총구매액이
    free_shipping_threshold 이상이면 배송비 0원.
    """
    # 매물이 있는 책과 없는 책 분리
    books_with_listings: set[int] = {lst.book_index for lst in listings}
    books_not_found = [
        book for i, book in enumerate(books) if i not in books_with_listings
    ]
    active_book_indices = sorted(books_with_listings)

    if not active_book_indices:
        return OptimizationResult(books_not_found=list(books))

    # 판매자 목록
    seller_ids = sorted({lst.seller_id for lst in listings})

    # 판매자별 배송비/임계값 (동일 판매자의 매물에서 대표값 사용)
    seller_shipping: dict[str, int] = {}
    seller_threshold: dict[str, int | None] = {}
    for lst in listings:
        sid = lst.seller_id
        if sid not in seller_shipping:
            seller_shipping[sid] = lst.shipping_cost
            seller_threshold[sid] = lst.free_shipping_threshold
        else:
            seller_shipping[sid] = min(seller_shipping[sid], lst.shipping_cost)
            # 임계값이 있는 매물이 하나라도 있으면 사용
            if lst.free_shipping_threshold is not None:
                if seller_threshold[sid] is None:
                    seller_threshold[sid] = lst.free_shipping_threshold
                else:
                    seller_threshold[sid] = min(
                        seller_threshold[sid], lst.free_shipping_threshold
                    )

    # 매물을 (book_index, seller_id) → listing 매핑
    listing_map: dict[tuple[int, str], Listing] = {}
    for lst in listings:
        key = (lst.book_index, lst.seller_id)
        if key not in listing_map or lst.price < listing_map[key].price:
            listing_map[key] = lst

    # MIP 모델 생성
    prob = LpProblem("BookBundler", LpMinimize)

    # 변수: x[i][j] = 책 i를 판매자 j에게서 구매 (binary)
    x: dict[tuple[int, int], LpVariable] = {}
    for bi in active_book_indices:
        for si, sid in enumerate(seller_ids):
            if (bi, sid) in listing_map:
                x[bi, si] = LpVariable(f"x_{bi}_{si}", cat=LpBinary)

    # 변수: y[j] = 판매자 j 사용 여부 (binary)
    y: dict[int, LpVariable] = {}
    for si in range(len(seller_ids)):
        y[si] = LpVariable(f"y_{si}", cat=LpBinary)

    # 변수: s[j] = 판매자 j에게 실제 부과되는 배송비 (continuous, >= 0)
    s: dict[int, LpVariable] = {}
    for si in range(len(seller_ids)):
        s[si] = LpVariable(f"s_{si}", lowBound=0, cat=LpContinuous)

    # big-M: 가능한 최대 배송비
    max_shipping = max(seller_shipping.values()) if seller_shipping else 10000

    # 상태 페널티: 1단계 차이에서 1000원 이하 가격 차이면 좋은 상태 선호
    # 새 책(0) = 최상(0) → 상(1001) → 중(2002) → 하(3003)
    condition_penalty = {"새 책": 0, "최상": 0, "상": 1001, "중": 2002, "하": 3003}

    # 목적함수: 책값 + 배송비 + 상태 페널티
    prob += (
        lpSum(
            listing_map[bi, seller_ids[si]].price * x[bi, si]
            for (bi, si) in x
        )
        + lpSum(s[si] for si in s)
        + lpSum(
            condition_penalty.get(
                listing_map[bi, seller_ids[si]].condition, 200
            ) * x[bi, si]
            for (bi, si) in x
        )
    )

    # 제약 1: 각 책은 정확히 1명에게서 구매
    for bi in active_book_indices:
        available = [si for (b, si) in x if b == bi]
        prob += lpSum(x[bi, si] for si in available) == 1, f"one_seller_{bi}"

    # 제약 2: 판매자 활성화
    for (bi, si) in x:
        prob += x[bi, si] <= y[si], f"activate_{bi}_{si}"

    # 제약 3: 배송비 결정
    for si in range(len(seller_ids)):
        sid = seller_ids[si]
        base_ship = seller_shipping[sid]
        threshold = seller_threshold.get(sid)

        if base_ship == 0:
            # 원래 무료배송인 판매자
            prob += s[si] == 0, f"free_ship_{si}"
        elif threshold is not None:
            # 조건부 무료배송: 총구매액 >= threshold이면 배송비 0
            # s[j] >= base_ship * y[j] - base_ship * (총구매액 / threshold)
            # 간단한 big-M 접근: f[j] = 무료배송 달성 여부 (binary)
            f = LpVariable(f"f_{si}", cat=LpBinary)

            # 판매자 j에서의 총구매액
            total_at_seller = lpSum(
                listing_map[bi, sid].price * x[bi, ssi]
                for (bi, ssi) in x if ssi == si
            )

            # f=1이면 총구매액 >= threshold
            # f=0이면 총구매액 < threshold
            max_possible = sum(
                listing_map[bi, sid].price
                for (bi, s_idx) in x if s_idx == si
            )
            big_m_price = max(max_possible, threshold) + 1

            # 총구매액 >= threshold - big_m_price * (1 - f)
            prob += total_at_seller >= threshold - big_m_price * (1 - f), \
                f"threshold_lb_{si}"
            # 총구매액 <= threshold - 1 + big_m_price * f
            prob += total_at_seller <= threshold - 1 + big_m_price * f, \
                f"threshold_ub_{si}"

            # 배송비: f=1이면 0, f=0이면 base_ship * y[j]
            # s[j] >= base_ship * y[j] - base_ship * f
            prob += s[si] >= base_ship * y[si] - base_ship * f, \
                f"ship_lb_{si}"
            # s[j] <= base_ship * y[j]
            prob += s[si] <= base_ship * y[si], f"ship_ub_{si}"
            # s[j] <= base_ship * (1 - f)
            prob += s[si] <= base_ship * (1 - f), f"ship_free_{si}"
        else:
            # 일반 유료배송: s[j] = base_ship * y[j]
            prob += s[si] == base_ship * y[si], f"ship_fixed_{si}"

    # 풀기
    prob.solve(PULP_CBC_CMD(msg=0))

    if LpStatus[prob.status] != "Optimal":
        return OptimizationResult(books_not_found=books_not_found)

    # 결과 추출
    assignments: list[Assignment] = []
    seller_actual_shipping: dict[str, int] = {}

    for si in s:
        val = value(s[si])
        if val is not None and val > 0.5:
            seller_actual_shipping[seller_ids[si]] = round(val)

    total_book_price = 0
    for (bi, si) in x:
        if value(x[bi, si]) is not None and value(x[bi, si]) > 0.5:
            sid = seller_ids[si]
            lst = listing_map[bi, sid]
            assignments.append(
                Assignment(
                    book=books[bi],
                    listing=lst,
                    seller_shipping=seller_actual_shipping.get(sid, 0),
                )
            )
            total_book_price += lst.price

    total_shipping = sum(seller_actual_shipping.values())
    total_cost = total_book_price + total_shipping

    # 개별 최저가 계산 (각 책의 가격+배송비가 가장 싼 매물)
    individual_cost = 0
    for bi in active_book_indices:
        book_listings = [lst for lst in listings if lst.book_index == bi]
        if book_listings:
            cheapest = min(book_listings, key=lambda l: l.price + l.shipping_cost)
            individual_cost += cheapest.price + cheapest.shipping_cost

    assignments.sort(key=lambda a: (a.listing.seller_id, a.listing.book_index))

    # 새 책 정가 합계
    total_original = sum(
        books[bi].original_price
        for bi in active_book_indices
        if books[bi].original_price is not None
    )

    return OptimizationResult(
        assignments=assignments,
        total_cost=total_cost,
        total_book_price=total_book_price,
        total_shipping=total_shipping,
        savings_vs_individual=individual_cost - total_cost,
        total_original_price=total_original,
        books_not_found=books_not_found,
    )
