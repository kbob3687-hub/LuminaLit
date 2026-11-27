import json

import numpy as np
import pysindy as ps

t = np.linspace(0, 1, 1001)
x = np.column_stack([np.exp(-2 * t), np.exp(t)])

model = ps.SINDy(feature_library=ps.PolynomialLibrary(degree=2))
model.fit(x, t=t)

with open("coefficients.json", "w", encoding="utf-8") as handle:
    json.dump(
        {"schema_version": 1, "coefficients": model.coefficients().tolist()},
        handle,
        allow_nan=False,
    )
print("Coefficient output written; independent verification is pending.")
