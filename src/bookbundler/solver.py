from __future__ import annotations

from pulp import HiGHS, LpSolver


def make_solver() -> LpSolver:
    """조합 최적화에 쓸 HiGHS 솔버를 만든다.

    PuLP에 동봉된 CBC는 macOS용이 x86_64 바이너리뿐이라 Rosetta가 없는
    Apple Silicon에서 실행되지 않고, PuLP 4.x는 PULP_CBC_CMD를 더 이상
    내보내지 않는다. highspy는 플랫폼별 휠로 배포되므로 HiGHS를 쓴다.
    """
    solver = HiGHS(msg=False)
    if not solver.available():
        raise RuntimeError(
            "HiGHS 솔버를 찾을 수 없습니다. `pip install highspy`로 설치해주세요."
        )
    return solver
