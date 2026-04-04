"""최적화 엔진 단위 테스트."""
from bookbundler.models import Book, Listing
from bookbundler.optimizer import optimize


def _book(title: str) -> Book:
    return Book(title=title)


def _listing(
    book_index: int,
    seller_id: str,
    price: int,
    shipping_cost: int,
    seller_name: str = "",
    condition: str = "상",
    free_shipping_threshold: int | None = None,
) -> Listing:
    return Listing(
        book_index=book_index,
        seller_id=seller_id,
        seller_name=seller_name or seller_id,
        price=price,
        condition=condition,
        shipping_cost=shipping_cost,
        free_shipping_threshold=free_shipping_threshold,
    )


class TestBasicOptimization:
    """기본 최적화 시나리오."""

    def test_single_book_single_seller(self):
        """책 1권, 판매자 1명 — 선택의 여지 없음."""
        books = [_book("데미안")]
        listings = [_listing(0, "s1", 3000, 2500)]

        result = optimize(books, listings)

        assert result.total_cost == 5500  # 3000 + 2500
        assert result.total_book_price == 3000
        assert result.total_shipping == 2500
        assert len(result.assignments) == 1

    def test_single_book_cheaper_total(self):
        """책 1권, 판매자 2명 — 총비용(가격+배송비)이 낮은 쪽 선택."""
        books = [_book("데미안")]
        listings = [
            _listing(0, "s1", 2000, 3000),  # 총 5000
            _listing(0, "s2", 3500, 0),  # 총 3500 (무료배송)
        ]

        result = optimize(books, listings)

        assert result.total_cost == 3500
        assert result.assignments[0].listing.seller_id == "s2"


class TestBundleOptimization:
    """묶음배송이 유리한 시나리오."""

    def test_bundle_saves_shipping(self):
        """2권을 같은 판매자에게서 사면 배송비 1회만 — 묶음이 유리."""
        books = [_book("데미안"), _book("노인과 바다")]
        listings = [
            # 판매자 A: 둘 다 있음
            _listing(0, "sA", 3000, 2500),
            _listing(1, "sA", 4000, 2500),
            # 판매자 B: 책0만 더 저렴
            _listing(0, "sB", 2000, 2500),
            # 판매자 C: 책1만 더 저렴
            _listing(1, "sC", 3000, 2500),
        ]
        # 개별 최저가: sB(2000+2500) + sC(3000+2500) = 10000
        # 묶음: sA(3000+4000+2500) = 9500 → 500원 절약

        result = optimize(books, listings)

        assert result.total_cost == 9500
        assert result.savings_vs_individual == 500
        # 둘 다 sA에서 구매
        seller_ids = {a.listing.seller_id for a in result.assignments}
        assert seller_ids == {"sA"}

    def test_split_is_cheaper(self):
        """가격 차이가 커서 분리 구매가 유리한 경우."""
        books = [_book("A"), _book("B")]
        listings = [
            # 판매자 X: 둘 다 비싸지만 배송비 1회
            _listing(0, "sX", 8000, 2500),
            _listing(1, "sX", 8000, 2500),
            # 판매자 Y: A만 매우 저렴
            _listing(0, "sY", 1000, 2500),
            # 판매자 Z: B만 매우 저렴
            _listing(1, "sZ", 1000, 2500),
        ]
        # 묶음 sX: 8000+8000+2500 = 18500
        # 분리: sY(1000+2500) + sZ(1000+2500) = 7000

        result = optimize(books, listings)

        assert result.total_cost == 7000
        seller_ids = {a.listing.seller_id for a in result.assignments}
        assert seller_ids == {"sY", "sZ"}


class TestEdgeCases:
    """엣지 케이스."""

    def test_no_listings(self):
        """매물이 하나도 없으면 빈 결과."""
        books = [_book("존재하지 않는 책")]
        result = optimize(books, [])

        assert len(result.assignments) == 0
        assert len(result.books_not_found) == 1

    def test_partial_coverage(self):
        """일부 책만 매물이 있는 경우."""
        books = [_book("데미안"), _book("없는 책"), _book("노인과 바다")]
        listings = [
            _listing(0, "s1", 3000, 2500),
            _listing(2, "s1", 4000, 2500),
        ]

        result = optimize(books, listings)

        assert len(result.assignments) == 2
        assert len(result.books_not_found) == 1
        assert result.books_not_found[0].title == "없는 책"

    def test_three_books_optimal_split(self):
        """3권 — 2권 묶음 + 1권 개별이 최적인 경우."""
        books = [_book("A"), _book("B"), _book("C")]
        listings = [
            # 판매자 P: A, B 보유 (가격 보통)
            _listing(0, "sP", 3000, 2500),
            _listing(1, "sP", 3000, 2500),
            # 판매자 Q: C만 보유 (저렴)
            _listing(2, "sQ", 2000, 2500),
            # 판매자 R: 전부 보유 (비쌈)
            _listing(0, "sR", 5000, 2500),
            _listing(1, "sR", 5000, 2500),
            _listing(2, "sR", 5000, 2500),
        ]
        # sP(3000+3000+2500) + sQ(2000+2500) = 13000
        # sR(5000+5000+5000+2500) = 17500

        result = optimize(books, listings)

        assert result.total_cost == 13000
        seller_ids = {a.listing.seller_id for a in result.assignments}
        assert seller_ids == {"sP", "sQ"}

    def test_same_seller_multiple_listings(self):
        """같은 판매자가 같은 책을 여러 가격에 등록한 경우 — 가장 싼 것 사용."""
        books = [_book("데미안")]
        listings = [
            _listing(0, "s1", 5000, 2500),
            _listing(0, "s1", 3000, 2500),  # 더 저렴
            _listing(0, "s1", 4000, 2500),
        ]

        result = optimize(books, listings)

        assert result.total_book_price == 3000


class TestFreeShippingThreshold:
    """무료배송 임계값 시나리오."""

    def test_below_threshold_pays_shipping(self):
        """구매액이 임계값 미만이면 배송비 부과."""
        books = [_book("인간 실격")]
        listings = [
            # 3,500원짜리 책, 배송비 3,200원, 25,000원 이상 무료배송
            _listing(0, "s1", 3500, 3200, free_shipping_threshold=25000),
        ]

        result = optimize(books, listings)

        assert result.total_book_price == 3500
        assert result.total_shipping == 3200
        assert result.total_cost == 6700

    def test_above_threshold_free_shipping(self):
        """구매액이 임계값 이상이면 배송비 무료."""
        books = [_book("A"), _book("B"), _book("C")]
        listings = [
            # 판매자 s1: 3권 합계 30,000원, 임계값 25,000원 → 무료배송
            _listing(0, "s1", 10000, 3000, free_shipping_threshold=25000),
            _listing(1, "s1", 10000, 3000, free_shipping_threshold=25000),
            _listing(2, "s1", 10000, 3000, free_shipping_threshold=25000),
        ]

        result = optimize(books, listings)

        assert result.total_book_price == 30000
        assert result.total_shipping == 0
        assert result.total_cost == 30000

    def test_threshold_affects_bundling_decision(self):
        """임계값 때문에 묶음이 유리해지는 케이스."""
        books = [_book("A"), _book("B")]
        listings = [
            # 판매자 X: 각 8,000원, 배송비 3,000원, 15,000원 이상 무료배송
            # 합계 16,000원 >= 15,000원 → 배송비 0원. 총 16,000원
            _listing(0, "sX", 8000, 3000, free_shipping_threshold=15000),
            _listing(1, "sX", 8000, 3000, free_shipping_threshold=15000),
            # 판매자 Y: A만 5,000원, 배송비 2,500원, 무료배송 없음
            _listing(0, "sY", 5000, 2500),
            # 판매자 Z: B만 5,000원, 배송비 2,500원, 무료배송 없음
            _listing(1, "sZ", 5000, 2500),
        ]
        # 분리: sY(5000+2500) + sZ(5000+2500) = 15,000원
        # 묶음: sX(8000+8000+0) = 16,000원
        # 분리가 유리

        result = optimize(books, listings)

        assert result.total_cost == 15000

    def test_threshold_bundling_wins(self):
        """임계값 덕분에 묶음이 이기는 케이스."""
        books = [_book("A"), _book("B")]
        listings = [
            # 판매자 X: 각 7,000원, 배송비 5,000원, 12,000원 이상 무료배송
            # 합계 14,000원 >= 12,000원 → 배송비 0원. 총 14,000원
            _listing(0, "sX", 7000, 5000, free_shipping_threshold=12000),
            _listing(1, "sX", 7000, 5000, free_shipping_threshold=12000),
            # 판매자 Y: A만 4,000원, 배송비 5,000원
            _listing(0, "sY", 4000, 5000),
            # 판매자 Z: B만 4,000원, 배송비 5,000원
            _listing(1, "sZ", 4000, 5000),
        ]
        # 분리: sY(4000+5000) + sZ(4000+5000) = 18,000원
        # 묶음: sX(7000+7000+0) = 14,000원 ← 승리

        result = optimize(books, listings)

        assert result.total_cost == 14000
        seller_ids = {a.listing.seller_id for a in result.assignments}
        assert seller_ids == {"sX"}


class TestConditionPreference:
    """상태 선호 시나리오."""

    def test_same_price_prefers_better_condition(self):
        """같은 가격이면 좋은 상태를 선택."""
        books = [_book("데미안")]
        listings = [
            _listing(0, "s1", 3000, 2500, condition="중"),
            _listing(0, "s2", 3000, 2500, condition="최상"),
        ]

        result = optimize(books, listings)

        assert result.assignments[0].listing.condition == "최상"
        assert result.assignments[0].listing.seller_id == "s2"

    def test_1000won_cheaper_prefers_better(self):
        """1단계 차이에서 1000원 차이면 좋은 상태 선택."""
        books = [_book("데미안")]
        listings = [
            _listing(0, "s1", 2500, 2500, condition="중"),
            _listing(0, "s2", 3500, 2500, condition="상"),  # 1000원 비쌈, 1단계 좋음
        ]

        result = optimize(books, listings)

        # 가격 차이 1000원 = 페널티 차이 1000원 → 동점, 좋은 상태 선택
        assert result.assignments[0].listing.condition == "상"

    def test_1001won_cheaper_prefers_price(self):
        """1단계 차이에서 1001원 차이면 가격 우선."""
        books = [_book("데미안")]
        listings = [
            _listing(0, "s1", 2499, 2500, condition="중"),
            _listing(0, "s2", 3500, 2500, condition="상"),  # 1001원 비쌈
        ]

        result = optimize(books, listings)

        # 가격 차이 1001원 > 페널티 차이 1000원 → 가격 우선
        assert result.assignments[0].listing.seller_id == "s1"

    def test_much_cheaper_bad_condition_wins(self):
        """가격 차이가 크면 가격 우선."""
        books = [_book("데미안")]
        listings = [
            _listing(0, "s1", 2000, 2500, condition="중"),
            _listing(0, "s2", 3500, 2500, condition="상"),  # 1500원 비쌈
        ]

        result = optimize(books, listings)

        assert result.assignments[0].listing.seller_id == "s1"
        assert result.total_cost == 4500
