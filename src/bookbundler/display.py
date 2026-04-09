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


def _condition_breakdown(result: OptimizationResult) -> dict[str, int]:
    """결과의 상태별 권수 집계."""
    counts: dict[str, int] = {}
    for a in result.assignments:
        cond = a.listing.condition
        counts[cond] = counts.get(cond, 0) + 1
    return counts


def _format_conditions(counts: dict[str, int]) -> str:
    """상태 집계를 '최상 2 · 상 3 · 중 1' 형태로 포맷."""
    order = ["새 책", "최상", "상", "중", "하"]
    parts = []
    for cond in order:
        if cond in counts:
            parts.append(f"{cond} {counts[cond]}")
    for cond, count in counts.items():
        if cond not in order:
            parts.append(f"{cond} {count}")
    return " · ".join(parts) if parts else "-"


def display_comparison(
    quality: OptimizationResult,
    cheapest: OptimizationResult,
) -> None:
    """상태 우선 vs 최저가 두 결과를 비교해서 출력한다.

    두 결과가 동일하면 상세 출력을 한 번만, 다르면 각각 상세 출력 후 비교.
    """
    # 결과가 같은지 확인 (판매자+매물 조합으로 판단)
    q_fingerprint = tuple(
        (a.listing.seller_id, a.listing.book_index, a.listing.price)
        for a in sorted(
            quality.assignments,
            key=lambda a: (a.listing.seller_id, a.listing.book_index),
        )
    )
    c_fingerprint = tuple(
        (a.listing.seller_id, a.listing.book_index, a.listing.price)
        for a in sorted(
            cheapest.assignments,
            key=lambda a: (a.listing.seller_id, a.listing.book_index),
        )
    )
    identical = q_fingerprint == c_fingerprint

    if identical:
        # 동일하면 한 번만 출력
        console.print()
        console.print(
            "[dim]상태 우선 전략과 최저가 전략의 결과가 동일합니다 — "
            "이미 최적 가격의 매물이 좋은 상태입니다.[/dim]"
        )
        display_result(quality)
        return

    # 상태 우선 결과 상세
    console.print()
    console.print("[bold cyan]━━━━━━  전략 1: 상태 우선  ━━━━━━[/bold cyan]")
    console.print("[dim]1000원 이내 차이면 좋은 상태를 선호[/dim]")
    display_result(quality)

    # 최저가 결과 상세
    console.print("[bold magenta]━━━━━━  전략 2: 최저가  ━━━━━━[/bold magenta]")
    console.print("[dim]상태 무시, 순수 최저가[/dim]")
    display_result(cheapest)

    # ── 비교 요약 ──
    console.print("[bold]━━━━━━  두 전략 비교  ━━━━━━[/bold]")
    console.print()

    diff = quality.total_cost - cheapest.total_cost
    quality_conds = _condition_breakdown(quality)
    cheapest_conds = _condition_breakdown(cheapest)

    table = Table(show_header=True, box=None, pad_edge=False)
    table.add_column("항목", style="dim", width=20)
    table.add_column("상태 우선", justify="right", style="cyan", width=22)
    table.add_column("최저가", justify="right", style="magenta", width=22)

    table.add_row(
        "총 비용",
        f"[bold]{quality.total_cost:,}원[/bold]",
        f"[bold]{cheapest.total_cost:,}원[/bold]",
    )
    table.add_row(
        "책값",
        f"{quality.total_book_price:,}원",
        f"{cheapest.total_book_price:,}원",
    )
    table.add_row(
        "배송비",
        f"{quality.total_shipping:,}원",
        f"{cheapest.total_shipping:,}원",
    )
    table.add_row(
        "상태 구성",
        _format_conditions(quality_conds),
        _format_conditions(cheapest_conds),
    )
    table.add_row(
        "판매자 수",
        f"{len({a.listing.seller_id for a in quality.assignments})}명",
        f"{len({a.listing.seller_id for a in cheapest.assignments})}명",
    )

    console.print(
        Panel(
            table,
            title="[bold]전략별 비교[/bold]",
            title_align="left",
            width=80,
            padding=(1, 2),
        )
    )

    console.print()
    if diff > 0:
        console.print(
            f"  [bold yellow]→ 상태를 포기하면 {diff:,}원 더 싸집니다[/bold yellow]"
        )
        per_book = diff // max(len(quality.assignments), 1)
        console.print(
            f"  [dim]  권당 평균 {per_book:,}원 절약 (상태 하락)[/dim]"
        )
    else:
        console.print(
            f"  [green]→ 상태 우선 결과가 오히려 {-diff:,}원 더 쌉니다[/green]"
        )
    console.print()
