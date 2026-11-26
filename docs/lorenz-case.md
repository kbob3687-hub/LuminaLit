# Lorenz 源码实验与交接说明

## 实验回答的问题

给定 Lorenz 系统的状态和带少量噪声的导数，固定版本的 PySINDy 源码能否重新识别
三个方程的稀疏系数？独立验收通过以后，使用同一冻结材料，在全新容器里是否仍通过？

- 原始论文：[Brunton、Proctor、Kutz，2016，Fig.1 / Lorenz equations](https://pmc.ncbi.nlm.nih.gov/articles/PMC4839439/)。
- 上游实现：[PySINDy v1.7.5](https://github.com/dynamicslab/pysindy/tree/4c32d2603cbf1aa476efae72bc78436cb1e6fc75)。
- 对应实验：[examples/3_original_paper.ipynb](https://github.com/dynamicslab/pysindy/blob/4c32d2603cbf1aa476efae72bc78436cb1e6fc75/examples/3_original_paper.ipynb)，Lorenz measured-derivative 部分的第一个噪声等级。
- 仓库提交固定为 `4c32d2603cbf1aa476efae72bc78436cb1e6fc75`。

本案例采用 Git partial clone / sparse checkout，仅检出 `pysindy` 源码、对应 notebook、
根依赖清单、忽略规则与许可证，不下载无关的大型实验数据。检出路径随确认单冻结，
当前运行和干净复跑一致。普通自定义仓库保持完整检出。
生成的 check.py、coefficients.json、experiment.json 路径也在 sparse 范围中，
使 Git 能正常导出补丁；不因此从上游下载这几个不存在的文件。

## 预先固定的设置

| 项目 | 值 |
|---|---|
| 方程 | x'=10(y−x)，y'=28x−y−xz，z'=xy−8z/3 |
| 初值 | (-8,8,27)，采用上游 notebook；原论文 Fig.1 为 (-8,7,27) |
| 时间 | 0 至 99.999，dt=0.001，共 100000 个点 |
| 积分 | solve_ivp / LSODA，rtol=atol=1e-12 |
| 导数 | 上游 lorenz 函数计算后添加标准差 1e-4 的高斯噪声 |
| 随机种子 | 在本子任务开始时重置为 1000 |
| 特征 | 五阶多项式库，共 56 项，含常数项 |
| 优化器 | 上游 STLSQ，稀疏阈值 0.05 |
| 验收 | 三行全部系数的最大绝对误差分别严格小于 0.001 |

验收阈值是维护者在执行前确定的接受标准，不是论文声称的误差值。零系数同样检查，
不能只展示几项拟合正确的系数。`check.py` 不含参考系数矩阵，不会根据验收结果调参。

## 与完整论文复现的边界

这是上游示例的一个低噪声系数识别子实验。维护者适配脚本调用检出仓库中的
SINDy、STLSQ、PolynomialLibrary 和 lorenz；启动使用 `-I`，随后只显式加入
检出源码目录，运行时检查关键对象的源码路径，拒绝回退到安装好的 pysindy wheel。

没有执行整本 notebook、全部噪声等级、前向轨迹模拟或原图绘制。随机种子在子任务前
重置，因此不依赖前面 notebook 章节消耗了多少随机数。实现为后续 PySINDy 版本，
不是原论文作者的 MATLAB 实现。指标检查不能单独认证训练过程或产物来源。

本案例无修复动作，不把它称为 AI 自动修复的成功案例；自动修复须使用另外的实际失败记录证明。

## 2026-11-26 本机验收记录

真实运行 `df4f3af66b2449379cf7e374da4b3793` 已完成当前验收、干净复跑与材料下载校验。
可检查的字段摘录见 [验收证据快照](evidence/lorenz-2026-11-26.json)，原始记录在本机
`runs/<run_id>/acceptance.json`。本次运行使用预装固定科学依赖的镜像，未调用模型。

| 指标 | 当前运行 | 干净复跑 | 固定要求 |
|---|---|---|---|
| x 方程最大系数误差 | 1.6198172048120796e-7 | 1.6198172048120796e-7 | < 0.001 |
| y 方程最大系数误差 | 4.2492834495533316e-7 | 4.2492834495533316e-7 | < 0.001 |
| z 方程最大系数误差 | 3.339405685309771e-6 | 3.339405685309771e-6 | < 0.001 |

两次系数产物 SHA-256 相同。把 y 方程的 x 系数加 1 后，独立验收器给出
`failed / metric_not_met`，该行误差约为 0.9999996。材料包完整性为 complete，
通过真实下载接口复核 ZIP SHA-256。总耗时 154.61 秒只是这次预装镜像条件下的观测，
不能用作通用性能承诺。此前网络超时、下载截断和 sparse 导出失败的记录均保留，未覆盖。

独立的本地案例预览可用 `python scripts/serve_lorenz.py --port 8016` 启动，
只注册 Lorenz 目标并使用禁止模型调用的执行器，不需要模型密钥。通用应用仍使用
`paperharness.app.main:app`。前端设置 `VITE_API_BASE_URL` 指向该预览端口，
同时在后端用 `PAPERHARNESS_CORS_ORIGINS` 放行实际的本地前端来源。
案例预览的首页 URL 加 `?live=1&case_preview=1`，隐藏通用仓库输入，明确只演示该确定性实验。

本轮检查：全量 pytest `655 passed, 22 skipped`；前端 16 项单测和 16 项浏览器场景通过，
生产构建与 Ruff 通过。另用实际本地 API 检查首页、确认单、结果页与 390px 布局，
浏览器无脚本错误；实际下载 ZIP 后复核其 SHA-256，确认前未创建运行任务。
跳过的测试不计作已验证能力；本轮没有完成跨机器交接、真实 AI 修复案例或公网部署。

## 一台新机器如何验证

需要 Git、Python 3.11+、Docker daemon，以及联网下载上游仓库和 PyPI 依赖的条件。
在本项目根目录执行：

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,docker]"
docker build -t paperharness/base-py3.10 -f docker/base-py3.10.Dockerfile docker/
# 可选：演示前预装相同的固定科学依赖，避免每个新容器重复下载百余 MB。
docker build -f docker/lorenz-warm.Dockerfile -t paperharness/base-py3.10 targets/paper-lorenz
.venv/Scripts/python scripts/accept_lorenz.py
```

网络较慢时，可在启动前选择能访问的 PyPI 索引，例如：

```powershell
$env:PAPERHARNESS_PIP_INDEX_URL='https://pypi.org/simple/'
# 仅在 HTTP/1.1 下载超时时，按网络条件切换；默认行为保持 HTTP/1.1
$env:PAPERHARNESS_GIT_HTTP_VERSION='HTTP/2'
```

脚本走真实 API 确认流程，先创建冻结确认单，再创建任务。它使用真实 Git 下载，
两次生产环境均为新容器，独立验收另用断网只读容器。失败时保留运行目录，不改阈值。

验收结束检查 `runs/<run_id>/acceptance.json`：

1. `outcome=succeeded`、当前 `verification.status=passed`。
2. `replay.status=passed`，且仍用 `sindy-lorenz-v1`。
3. 三个方程的原始误差值均小于固定阈值。
4. 错误指标反例为 `failed / metric_not_met`。
5. 材料 ZIP 的 SHA-256 与 API 记录一致。

`experiment.json` 记录实际源码路径及哈希、上游 notebook 哈希、数据字节摘要与设置。
该文件目前随材料包的 `files/experiment.json` 提供，由生产脚本生成，不是独立来源证明。
完整源码、数据与依赖并未全部离线封装；外部条件仍需满足。
预装依赖镜像也必须记录实际镜像 ID；它不含 pysindy wheel。使用预装镜像时，
应如实说明依赖已预装，不能把它描述为无缓存的在线安装测试。

同一宿主机的新容器复跑不等于跨机器交接。其他同学应在独立机器按上述步骤执行，
保存自己的 run_id、环境、误差与失败情况后，才能新增“跨机器通过”的结论。

## 演示顺序

1. 首页选择 Lorenz 实验，解释目标和范围。
2. 确认页展示固定提交、适配脚本与依赖快照、验收阈值。
3. 真实运行中说明当前阶段；首次下载可能花费几分钟。
4. 结果页核对三个误差、当前验收与干净复跑的独立结论。
5. 下载材料，展示 experiment.json 和验收输入；再展示错误系数被拒绝的反例。

录屏压缩等待时间时注明剪辑。预先完成的真实运行应标明历史记录，重新执行应创建新任务。

## 后续需要人的证据

- 另一台机器完成独立验证及交接反馈。
- 3–5 名科研用户试用：是否看懂结论、哪里卡住、是否需要口头指导。
- 用另一个实际失败案例证明 AI 修复能力，保留原始错误、真实模型动作及最终 diff。

不预填用户反馈、耗时改善比例、跨机器结论或获奖概率。
