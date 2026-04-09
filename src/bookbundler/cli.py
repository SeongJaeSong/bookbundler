from __future__ import annotations

import click
from rich.console import Console

from bookbundler.display import display_comparison, display_result
from bookbundler.optimizer import optimize
from bookbundler.scraper import _is_isbn, scrape_books

console = Console()


@click.group()
def main() -> None:
    """BookBundler — 중고책 배송비 최적화 도구."""


@main.command()
@click.argument("queries", nargs=-1, required=True)
@click.option(
    "--condition",
    "-c",
    type=click.Choice(["최상", "상", "중", "하"]),
    default=None,
    help="책 상태 필터",
)
@click.option(
    "--platform",
    "-p",
    type=click.Choice(["all", "aladin", "yes24"]),
    default="all",
    help="검색 플랫폼 (기본: 전체)",
)
@click.option(
    "--strategy",
    "-s",
    type=click.Choice(["quality", "cheapest", "compare"]),
    default="compare",
    help="quality=상태 우선, cheapest=최저가, compare=두 전략 비교 (기본)",
)
def search(
    queries: tuple[str, ...],
    condition: str | None,
    platform: str,
    strategy: str,
) -> None:
    """책 제목이나 ISBN으로 검색하여 최적 구매 조합을 찾습니다.

    쉼표로 구분하면 따옴표 없이 여러 권을 입력할 수 있습니다.

    \b
    예시:
        bookbundler search 데미안,노인과 바다,이방인
        bookbundler search 9788937460470,노인과 바다
        bookbundler search "데미안" "노인과 바다"
        bookbundler search -p aladin 데미안,이방인
    """
    # 쉼표로 구분된 입력 처리: 인자들을 합쳐서 쉼표로 분리
    raw = " ".join(queries)
    if "," in raw:
        parsed = [q.strip() for q in raw.split(",") if q.strip()]
    else:
        parsed = list(queries)

    if not parsed:
        console.print("[red]최소 1권 이상의 책을 입력해주세요.[/red]")
        return

    platforms = None if platform == "all" else [platform]
    platform_label = "알라딘 + YES24" if platform == "all" else platform

    console.print(f"\n[bold]검색 중...[/bold] {len(parsed)}권 ({platform_label})")
    for q in parsed:
        label = "[dim](ISBN)[/dim]" if _is_isbn(q) else ""
        console.print(f"  - {q} {label}")
    console.print()

    with console.status(f"[bold green]{platform_label}에서 매물을 검색하는 중..."):
        books, listings = scrape_books(
            parsed, condition_filter=condition, platforms=platforms,
        )

    if not listings:
        console.print("[red]매물을 찾지 못했습니다.[/red]")
        return

    listing_count = len(listings)
    seller_count = len({lst.seller_id for lst in listings})
    console.print(
        f"[dim]수집 완료: {listing_count}개 매물, {seller_count}명 판매자[/dim]"
    )

    if strategy == "compare":
        with console.status("[bold green]두 전략을 비교 계산하는 중..."):
            quality_result = optimize(books, listings, strategy="quality")
            cheapest_result = optimize(books, listings, strategy="cheapest")
        display_comparison(quality_result, cheapest_result)
    else:
        with console.status("[bold green]최적 조합을 계산하는 중..."):
            result = optimize(books, listings, strategy=strategy)
        display_result(result)


if __name__ == "__main__":
    main()
