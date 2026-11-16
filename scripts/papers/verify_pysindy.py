"""PySINDy 最小复现样例：从数值数据反推 ODE 方程。

论文：PySINDy: A Python package for the sparse identification of nonlinear
      dynamical systems from data（JOSS 10.21105/joss.02104，arXiv:2004.08424）
仓库：https://github.com/dynamicslab/pysindy

数据全部由 numpy 合成，不访问网络、不下载数据集，CPU 秒级完成。

判据（可机读）：系数矩阵命中 -2.0（对应 x' = -2x）与 1.0（对应 y' = y），容差 1e-2。
退出码：0 通过，1 失败。

用法：
    python scripts/papers/verify_pysindy.py
    python scripts/papers/verify_pysindy.py --artifact out.txt
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

ATOL = 1e-2
EXPECTED = (-2.0, 1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="PySINDy 最小复现样例")
    parser.add_argument(
        "--artifact",
        help="把结果写到指定文件（给靶场的 expected_artifact 用）",
    )
    args = parser.parse_args()

    try:
        import pysindy as ps
    except ImportError:
        print("[FAIL] 缺少 pysindy，先执行：")
        print("       python -m pip install -r scripts/papers/requirements.txt")
        return 1

    t = np.linspace(0, 1, 100)
    x = 3 * np.exp(-2 * t)
    y = 0.5 * np.exp(t)
    X = np.stack((x, y), axis=-1)

    model = ps.SINDy()
    model.fit(X, t=t, feature_names=["x", "y"])

    print("--- model.print() ---")
    model.print()

    coefs = model.coefficients()
    print("--- coefficients ---")
    print(coefs)

    hit_x = bool(np.any(np.isclose(coefs, EXPECTED[0], atol=ATOL)))
    hit_y = bool(np.any(np.isclose(coefs, EXPECTED[1], atol=ATOL)))
    passed = hit_x and hit_y

    print()
    print(f"判据: 命中 {EXPECTED[0]} = {hit_x}，命中 {EXPECTED[1]} = {hit_y}，容差 {ATOL}")

    if args.artifact:
        lines = ["pysindy", f"hit_minus_2={hit_x}", f"hit_1={hit_y}", f"pass={passed}"]
        with open(args.artifact, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"已写入产物: {args.artifact}")

    if passed:
        print("[PASS] PySINDy 最小样例跑通，两个系数都对上")
        return 0
    print("[FAIL] 系数与预期不符，检查 numpy / scikit-learn 版本与上面的系数矩阵")
    return 1


if __name__ == "__main__":
    sys.exit(main())
