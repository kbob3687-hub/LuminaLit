# 真实论文复现样例（两个候选）

README 的「待补」里有一条：**2 个真实论文靶场替换**。内置 8 个靶场是自建仓库，失败类型可控，但代码不是真论文代码——评委大概率会问「你们真跑过论文吗」。这份文档给两个**已在本机实测跑通**的候选，以及把它们变成靶场之前必须处理的约束。

配套脚本：`scripts/papers/verify_pysindy.py`、`scripts/papers/verify_umap.py`、`scripts/papers/requirements.txt`。

---

## 1. 两个候选

| | 候选 A | 候选 B |
|---|---|---|
| 论文 | PySINDy（JOSS 10.21105/joss.02104 / arXiv:2004.08424） | UMAP（arXiv:1802.03426） |
| 仓库 | `github.com/dynamicslab/pysindy` | `github.com/lmcinnes/umap` |
| 它干什么 | 只有数值，反推出控制方程的少数几项 | 把高维数据压到 2 维，保住邻域结构 |
| 核心方法 | 候选函数库 + 稀疏回归（STLSQ），小系数砍成 0 | 模糊拓扑图 + 低维布局最小化交叉熵 |
| 依赖 | numpy / scipy / scikit-learn / derivative | 上述 + numba / pynndescent / tqdm |
| 数据 | numpy 合成，零下载 | make_blobs 合成，零下载（不下 MNIST） |
| 本机耗时 | 秒级 | 8.1 秒（含 numba 首次 JIT） |
| 判据 | 系数命中 −2.0 与 1.0，容差 1e-2 | 形状 (200, 2) 且 ARI ≥ 0.9 |
| 建议失败类型 | `none`（干净正向样例） | `version_conflict`（见第 4 节） |

## 2. 为什么是这两篇

按四条筛选标准逐条对：

1. **CPU 几分钟能跑通**：都只看合成数据的小例子，秒级到十秒级。不碰扩散模型、大模型微调、3D 重建。
2. **不带 GB 级权重**：两篇都不下载任何 checkpoint；UMAP 官方 README 那个 `fetch_openml('mnist_784')` 例子明确避开，改用 `make_blobs`。
3. **依赖干净**：全是预编译轮子，没有 faiss / detectron2 / mmcv / pycocotools 这类要现场编译 C++ 的东西。
4. **结果可机读判定**：这是最关键的一条。PySINDy 判系数，UMAP 判 ARI，都是一行断言，不需要人眼看图说「好像对了」。队长的测试题、前端的 PASS/FAIL、验收口径建立在同一个判据上。

两篇还互补：**一个输出数字（确定性强），一个输出嵌入（默认带随机性，必须固定 `random_state`）**。后者正好是「论文复现危机」的活例子——同一份代码、同一个版本都可能不一样，跨版本跨机器更不用说。

## 3. 本机实测记录

环境：Windows，Python 3.13.7，pysindy 2.1.0，umap-learn 0.6.0，2026-11-16。

PySINDy：

```
(x)' = -2.000 x
(y)' =  1.000 y
[PASS] 两个系数都对上
```

系数是 −2.00012 而不是整数 −2，因为导数只能用有限差分估算，本身带截断误差。**这个误差是好事**：证明结果是从数据反推的，不是抄答案。

UMAP：

```
输入形状: (200, 10) -> 降维后: (200, 2)
耗时: 8.1 秒（首次运行含 numba JIT 编译时间）
2 维嵌入上重新聚类的 ARI = 1.000
```

另外 umap-learn 0.6.0 会报一个 `compatibility_layout` 的 FutureWarning，原文说得很明确：当前走的是 0.5.x 的 legacy 近邻/初始化/layout 流程，未来版本默认改成递归初始化 + Adam 优化器，**embedding 会不一样**。这是真实的版本漂移风险，不是我们造的，答辩时可以直接当「论文复现危机」的实证。

顺带还有一条 `n_jobs value -1 overridden to 1 by setting random_state`——固定随机种子会顺带把并行关掉。要复现就得认这个代价，这也是「可复现」和「跑得快」之间的真实取舍。

## 4. 变成靶场之前必须处理的 6 个约束

这几条不处理，现场一定翻车：

1. **官方仓库根目录没有 `main.py`**。`TargetManifest.command` 在容器里的仓库根执行，而 pysindy / umap 都是库，没有入口脚本。建议在 `paperharness-targets` 里为两篇各开一个分支，放一个 `main.py`（把 `scripts/papers/verify_*.py` 的核心逻辑搬进去，去掉 argparse）+ 锁定版本的 `requirements.txt`。靶场仍然是「真仓库 + 一行入口」，不改上游一行代码。
2. **断网**。依赖必须预装进基础镜像，或靠 `install_requirement` 临时开网。`pip install` 一次装两篇的全部依赖不现实（scipy/sklearn/numba 都不小），建议镜像里预装 `scripts/papers/requirements.txt`。
3. **`expected_artifact` 要落在文件上**。纯 stdout 不算产物。两个脚本都支持 `--artifact out.txt`，wrapper 的 `main.py` 直接写 `out.txt` 即可。
4. **`revision` 必须是 40 位完整 commit**。不能用分支名，也不能用短 SHA。
5. **超时**。numba 首次 JIT 在本机 8 秒，单核容器里可能 30–60 秒。UMAP 那篇的 `timeout_seconds` 别按 PySINDy 的量级给。
6. **numpy 与 numba 的版本上界**。两个包都要 `numpy>=2.0`，UMAP 还要 `numba>=0.60`，而 numba 对 numpy 有上界。这是整条依赖链最容易炸的地方，也正好是现成的 `version_conflict` 素材：在 UMAP 靶场里把 `requirements.txt` 写成 `numpy==1.26.4`，装完 UMAP 直接 import 失败，自愈循环要做的就是把约束改成兼容版本——真实、可解释、不造作。

## 5. manifest 草案

`revision` 留空待靶场仓库建好后填。PySINDy 正向样例：

```json
{
  "id": "real-pysindy",
  "repo_url": "https://github.com/kbob3687-hub/paperharness-targets",
  "revision": "TODO_40_BIT_COMMIT",
  "python_version": "3.10",
  "command": ["python", "main.py"],
  "expected_artifact": "out.txt",
  "failure_class": "none",
  "repairable": true,
  "allowed_actions": [],
  "expected_final": "succeeded",
  "notes": "真实论文 PySINDy（arXiv:2004.08424）最小样例，合成数据反推 x'=-2x / y'=y，预期零修复一次成功。"
}
```

UMAP 版本冲突样例：

```json
{
  "id": "real-umap-version-conflict",
  "repo_url": "https://github.com/kbob3687-hub/paperharness-targets",
  "revision": "TODO_40_BIT_COMMIT",
  "python_version": "3.10",
  "command": ["python", "main.py"],
  "expected_artifact": "out.txt",
  "failure_class": "version_conflict",
  "repairable": true,
  "allowed_actions": ["read_file", "search_text", "install_requirement", "replace_exact", "run_smoke_test"],
  "expected_final": "succeeded",
  "notes": "真实论文 UMAP（arXiv:1802.03426）最小样例。requirements.txt 把 numpy 钉在 1.26.4，与 umap-learn/numba 要求的 numpy>=2.0 冲突，预期靠改约束 + 重装修复。"
}
```

## 6. 复跑方式

```
python -m pip install -r scripts/papers/requirements.txt
python scripts/papers/verify_pysindy.py
python scripts/papers/verify_umap.py
```

两个脚本都是退出码 0 = 通过、1 = 失败，可以直接挂到 CI 或靶场 `main.py` 里。
