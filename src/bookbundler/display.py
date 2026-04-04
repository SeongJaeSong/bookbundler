from __future__ import annotations

from itertools import groupby

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from bookbundler.models import OptimizationResult

console = Console()


def display_result(result: OptimizationResult) -> None:
    """최적화 결과를 터미널에 출력한다."""
    if not result.assignments:
        console.print("[red]매물을 찾지 못했습니다.[/red]")
        if result.books_not_found:
            for book in result.books_not_found:
                console.print(f"  - {book.title}")
        return

    # 매물 못 찾은 책 경고
    if result.books_not_found:
        console.print()
        console.print("[yellow]다음 책은 중고 매물을 찾지 못했습니다:[/yellow]")
        for book in result.books_not_found:
            console.print(f"  - {book.title}")
        console.print()

    found_count = len(result.assignments)
    seller_count = len({a.listing.seller_id for a in result.assignments})
    console.print(
        f"\n[bold]검색 결과:[/bold] {found_count}권, "
        f"판매자 {seller_count}명에게서 구매"
    )

    # ── 판매자별 구매 가이드 ──
    console.print()
    sorted_assignments = sorted(
        result.assignments, key=lambda a: a.listing.seller_id
    )
    bundle_num = 0
    for seller_id, group in groupby(
        sorted_assignments, key=lambda a: a.listing.seller_id
    ):
        group_list = list(group)
        bundle_num += 1
        first = group_list[0]

        # 플랫폼 식별
        platform = ""
        if seller_id.startswith("aladin:"):
            platform = "알라딘"
        elif seller_id.startswith("yes24:"):
            platform = "YES24"

        # 소계 계산
        book_subtotal = sum(a.listing.price for a in group_list)
        shipping = first.seller_shipping
        subtotal = book_subtotal + shipping

        # 테이블 구성
        table = Table(
            show_header=True,
            show_lines=False,
            pad_edge=False,
            box=None,
        )
        table.add_column("책", style="cyan", min_width=20)
        table.add_column("상태", width=4)
        table.add_column("가격", justify="right", width=10)

        for assign in group_list:
            table.add_row(
                assign.book.title,
                assign.listing.condition,
                f"{assign.listing.price:,}원",
            )
            if assign.listing.url:
                table.add_row(
                    f"  [dim link={assign.listing.url}]{assign.listing.url}[/dim link]",
                    "",
                    "",
                )

        # 배송비 텍스트
        if shipping == 0:
            shipping_text = "[green]무료배송[/green]"
        else:
            shipping_text = f"배송비 {shipping:,}원"

        # 패널 제목
        title = (
            f"[bold]주문 {bundle_num}[/bold]  "
            f"[blue]{platform}[/blue] — {first.listing.seller_name}  "
            f"({len(group_list)}권)"
        )
        # 패널 하단
        subtitle = (
            f"책값 {book_subtotal:,}원 + {shipping_text} = "
            f"[bold]{subtotal:,}원[/bold]"
        )

        panel = Panel(
            table,
            title=title,
            subtitle=subtitle,
            title_align="left",
            subtitle_align="right",
            width=80,
            padding=(0, 1),
        )
        console.print(panel)

    # ── 총 비용 요약 ──
    console.print()
    summary_lines = [
        f"  책값 합계:   {result.total_book_price:,}원",
        f"  배송비 합계: {result.total_shipping:,}원",
        f"  [bold]총 비용:     {result.total_cost:,}원[/bold]",
    ]

    if result.total_original_price > 0:
        savings_vs_new = result.total_original_price - result.total_cost
        discount_pct = round(savings_vs_new / result.total_original_price * 100)
        summary_lines.append("")
        summary_lines.append(
            f"  [dim]새 책 정가 합계: {result.total_original_price:,}원[/dim]"
        )
        summary_lines.append(
            f"  [bold yellow]새 책 대비 {savings_vs_new:,}원 절약 ({discount_pct}% 할인)[/bold yellow]"
        )

    if result.savings_vs_individual > 0:
        individual_total = result.total_cost + result.savings_vs_individual
        summary_lines.append("")
        summary_lines.append(
            f"  [dim]묶음 없이 개별 구매 시: {individual_total:,}원[/dim]"
        )
        summary_lines.append(
            f"  [bold green]묶음 최적화로 {result.savings_vs_individual:,}원 절약![/bold green]"
        )

    console.print(
        Panel(
            "\n".join(summary_lines),
            title="[bold]비용 요약[/bold]",
            title_align="left",
            width=80,
            padding=(0, 1),
        )
    )
    console.print()
