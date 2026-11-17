"""PySINDy (arXiv:2004.08424) README 最小例子复现断言。

SINDy 从线性系统 x' = -2x, y' = y 的采样解中识别动力学方程，
系数误差须 < 1e-4。PASS 时把方程写入 out.txt；任何断言失败非 0 退出。
"""

import numpy as np
import pysindy as ps

# 线性系统的解析解：x(t)=exp(-2t), y(t)=exp(t)。
# 1001 个采样点：有限差分的导数误差须压到 1e-4 以下才能断言系数精度。
t = np.linspace(0, 1, 1001)
x = np.column_stack([np.exp(-2 * t), np.exp(t)])

model = ps.SINDy()
model.fit(x, t=t)
model.print()

coefficients = model.coefficients()  # shape (n_eq, n_features)；特征序 [1, x0, x1, ...]
error_x = abs(coefficients[0, 1] - (-2.0))  # x' 方程的 x0 系数
error_y = abs(coefficients[1, 2] - 1.0)  # y' 方程的 x1 系数
assert error_x < 1e-4, f"x' coefficient error {error_x}"
assert error_y < 1e-4, f"y' coefficient error {error_y}"

equations = "; ".join(model.equations())
with open("out.txt", "w", encoding="utf-8") as handle:
    handle.write(
        "PySINDy minimal example PASS\n"
        f"{equations}\n"
        f"coefficient errors: {error_x:.2e}, {error_y:.2e}\n"
    )
print(f"PASS {equations}")
