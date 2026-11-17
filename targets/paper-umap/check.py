"""UMAP (arXiv:1802.03426) 合成数据降维复现断言。

200 个 10 维合成样本降到 2 维，KMeans 重新聚类；
调整兰德指数 ARI 须 >= 0.999（簇结构完整保留），不用下载任何数据集。
PASS 时写 out.txt；任何断言失败非 0 退出。
"""

import umap
from sklearn.cluster import KMeans
from sklearn.datasets import make_blobs
from sklearn.metrics import adjusted_rand_score

X, y = make_blobs(n_samples=200, n_features=10, centers=3, random_state=42)

reducer = umap.UMAP(n_components=2, random_state=42)
embedding = reducer.fit_transform(X)

labels = KMeans(n_clusters=3, n_init=10, random_state=42).fit_predict(embedding)
ari = adjusted_rand_score(y, labels)

assert embedding.shape == (200, 2), f"bad shape {embedding.shape}"
assert ari >= 0.999, f"cluster structure lost: ARI={ari}"

with open("out.txt", "w", encoding="utf-8") as handle:
    handle.write(
        "UMAP synthetic reduction PASS\n"
        f"embedding shape: {embedding.shape}\n"
        f"ARI after re-clustering: {ari:.3f}\n"
    )
print(f"PASS ARI={ari:.3f}")
