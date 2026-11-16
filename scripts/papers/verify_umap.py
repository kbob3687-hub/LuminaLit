"""UMAP 最小复现样例：把高维数据降到 2 维，并检查簇结构是否保住。

论文：UMAP: Uniform Manifold Approximation and Projection for Dimension
      Reduction（arXiv:1802.03426）
仓库：https://github.com/lmcinnes/umap

数据由 sklearn.datasets.make_blobs 合成，不下载 MNIST / Fashion-MNIST 等任何外部数据。

判据（可机读）：输出形状为 (n_samples, 2)，且 2 维嵌入上重新聚类与真实标签的
调整兰德指数 ARI >= 0.9。退出码：0 通过，1 失败。

注意：UMAP 默认 random_state=None，每次结果不同；这里固定 42 以保证可复现。

用法：
    python scripts/papers/verify_umap.py
    python scripts/papers/verify_umap.py --artifact out.txt
"""

from __future__ import annotations

import argparse
import sys
import time

from sklearn.cluster import KMeans
from sklearn.datasets import make_blobs
from sklearn.metrics import adjusted_rand_score

N_SAMPLES = 200
N_FEATURES = 10
N_CENTERS = 3
ARI_THRESHOLD = 0.9


def main() -> int:
    parser = argparse.ArgumentParser(description="UMAP 最小复现样例")
    parser.add_argument(
        "--artifact",
        help="把结果写到指定文件（给靶场的 expected_artifact 用）",
    )
    args = parser.parse_args()

    try:
        import umap
    except ImportError:
        print("[FAIL] 缺少 umap-learn，先执行：")
        print("       python -m pip install -r scripts/papers/requirements.txt")
        return 1

    X, labels = make_blobs(
        n_samples=N_SAMPLES, n_features=N_FEATURES, centers=N_CENTERS, random_state=42
    )

    started = time.time()
    embedding = umap.UMAP(n_neighbors=15, min_dist=0.1, random_state=42).fit_transform(X)
    elapsed = time.time() - started

    shape_ok = embedding.shape == (N_SAMPLES, 2)
    print(f"输入形状: {X.shape} -> 降维后: {embedding.shape}")
    print(f"耗时: {elapsed:.1f} 秒（首次运行含 numba JIT 编译）")

    ari = 0.0
    if shape_ok:
        pred = KMeans(n_clusters=N_CENTERS, n_init=10, random_state=42).fit_predict(embedding)
        ari = float(adjusted_rand_score(labels, pred))
    print(f"2 维嵌入上重新聚类的 ARI = {ari:.3f}")

    passed = shape_ok and ari >= ARI_THRESHOLD
    print()
    print(f"判据: 形状正确 = {shape_ok}，ARI >= {ARI_THRESHOLD} = {ari >= ARI_THRESHOLD}")

    if args.artifact:
        lines = [
            "umap",
            f"shape_ok={shape_ok}",
            f"ari={ari:.4f}",
            f"seconds={elapsed:.2f}",
            f"pass={passed}",
        ]
        with open(args.artifact, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"已写入产物: {args.artifact}")

    if passed:
        print("[PASS] UMAP 最小样例跑通，簇结构在 2 维里保住")
        return 0
    if shape_ok:
        print("[FAIL] 形状对但 ARI 偏低，检查 n_neighbors / 数据分布")
        return 1
    print("[FAIL] 输出形状不对，检查 umap-learn 版本")
    return 1


if __name__ == "__main__":
    sys.exit(main())
