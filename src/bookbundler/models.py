from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Book:
    """검색 대상 책."""

    title: str
    isbn: str | None = None
    author: str | None = None
    publisher: str | None = None
    original_price: int | None = None  # 새 책 정가


@dataclass
class Listing:
    """특정 책의 특정 판매자 등록 매물."""

    book_index: int  # 입력 책 목록에서의 인덱스
    seller_id: str
    seller_name: str
    price: int  # 원
    condition: str  # 상/중/하 또는 최상 등
    shipping_cost: int  # 원 (해당 판매자 기본 배송비)
    free_shipping_threshold: int | None = None  # 무료배송 기준 금액 (None이면 조건부 무료배송 없음)
    is_aladin_direct: bool = False  # 알라딘 직접매입 여부
    url: str = ""


@dataclass
class SellerShippingRule:
    """판매자의 배송비 규칙."""

    seller_id: str
    base_shipping_cost: int  # 기본 배송비
    free_shipping_threshold: int | None = None  # 무료배송 기준 금액 (None이면 무료배송 없음)


@dataclass
class Assignment:
    """최적화 결과에서 하나의 책-매물 배정."""

    book: Book
    listing: Listing
    seller_shipping: int  # 이 판매자에게 부과된 배송비 (묶음 시 분담)


@dataclass
class OptimizationResult:
    """최적화 결과."""

    assignments: list[Assignment] = field(default_factory=list)
    total_cost: int = 0  # 책값 + 배송비 합계
    total_book_price: int = 0
    total_shipping: int = 0
    savings_vs_individual: int = 0  # 개별 최저가 구매 대비 절약액
    total_original_price: int = 0  # 새 책 정가 합계 (정가를 알 수 있는 책만)
    books_not_found: list[Book] = field(default_factory=list)  # 매물을 찾지 못한 책
