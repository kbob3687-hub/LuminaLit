"""Maintained adapter for one Lorenz experiment in examples/3_original_paper.ipynb.

Execute only inside the producer sandbox. This is not the full notebook, nor the
original authors' MATLAB code. All model coefficients are learned from data.
"""

import hashlib
import inspect
import json
import sys
from pathlib import Path

# -I removes cwd from sys.path. Add ONLY the explicitly checked-out source tree.
workspace = Path(__file__).resolve().parent
sys.path.insert(0, str(workspace))

import numpy as np
import pysindy as ps
from pysindy.utils import lorenz
from scipy.integrate import solve_ivp

source_files = {}
for component in (ps, ps.SINDy, ps.STLSQ, ps.PolynomialLibrary, lorenz):
    path = Path(inspect.getfile(component)).resolve()
    relative = path.relative_to(workspace)  # installed-wheel fallback is forbidden
    source_files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()

# Upstream notebook: Lorenz section, measured derivatives, first noise level.
dt = 0.001
t_train = np.arange(0, 100, dt)
solution = solve_ivp(
    lorenz, (t_train[0], t_train[-1]), [-8, 8, 27], t_eval=t_train,
    method="LSODA", rtol=1e-12, atol=1e-12,
)
if not solution.success or solution.y.shape != (3, 100000):
    raise RuntimeError("Lorenz data integration incomplete")
x_train = solution.y.T
x_dot = np.array([lorenz(0, row) for row in x_train])
# Reset locally: independent of random draws made by other notebook sections.
np.random.seed(1000)
noisy_derivatives = x_dot + np.random.normal(scale=1e-4, size=x_train.shape)
model = ps.SINDy(
    optimizer=ps.STLSQ(threshold=0.05),
    feature_library=ps.PolynomialLibrary(degree=5),
)
model.fit(x_train, t=dt, x_dot=noisy_derivatives, quiet=True)

Path("coefficients.json").write_text(json.dumps({
    "schema_version": 1, "coefficients": model.coefficients().tolist(),
}, allow_nan=False), encoding="utf-8")
evidence = {
    "experiment": "SINDy Lorenz measured-derivative low-noise subexperiment",
    "upstream_notebook": "examples/3_original_paper.ipynb",
    "upstream_notebook_sha256": hashlib.sha256(
        (workspace / "examples/3_original_paper.ipynb").read_bytes()
    ).hexdigest(),
    "executed_source_sha256": source_files,
    "data_sha256": hashlib.sha256(
        np.ascontiguousarray(np.column_stack((t_train, x_train, noisy_derivatives)),
                             dtype="<f8").tobytes()
    ).hexdigest(),
    "samples": len(t_train), "dt": dt, "initial_condition": [-8, 8, 27],
    "derivative_noise_std": 1e-4, "seed": 1000,
    "polynomial_degree": 5, "optimizer_threshold": 0.05,
    "features": model.get_feature_names(),
    "adaptations": [
        "Only first derivative-noise level; no forward simulation or plot recreation.",
        "RNG reset before Lorenz fitting, independently of preceding notebook sections.",
        "Upstream example uses (-8,8,27); original paper Figure 1 uses (-8,7,27).",
    ],
}
Path("experiment.json").write_text(
    json.dumps(evidence, indent=2, allow_nan=False), encoding="utf-8",
)
print(json.dumps(evidence, allow_nan=False))
model.print()
