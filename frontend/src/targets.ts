/**
 * 内置靶场的展示元数据。
 *
 * 为什么需要这张表：
 * `GET /api/targets` 只回 `id / repo_url / command / expected_artifact`，
 * 但除了 paper-pysindy、paper-umap 之外，其余 8 个靶场共用同一个 repo_url，
 * 真正决定"检出哪份代码"的是 `revision`。后端 `runner._match_target` 按
 * repo_url 匹配靶场，命中后仍用请求里的 revision 去 clone，所以前端提交时
 * 必须带上 revision，否则永远只能跑到第一个靶场。
 *
 * 后端若在 /api/targets 里补上 revision / failure_class，本表即可删除。
 * 表里的值抄自 `targets/<id>/target.json`，改动靶场时需要同步。
 */

export interface TargetMeta {
  /** 提交给 POST /api/runs 的 revision，决定 clone 到哪个 commit。 */
  revision: string;
  /** 一句话说明这个靶场在考什么，卡片上直接显示。 */
  blurb: string;
  /** 靶场设计上预期的终点：能修好 / 本来就不可复现。 */
  expect: "succeeded" | "failed";
  preflight?: { timeout_seconds: number; max_repairs: number };
}

export const TARGET_META: Record<string, TargetMeta> = {
  "paper-lorenz": {
    revision: "4c32d2603cbf1aa476efae72bc78436cb1e6fc75",
    blurb: "SINDy 论文 Lorenz 系数识别子实验：运行检出源码，独立检查三个方程，再在全新容器复跑。",
    expect: "succeeded",
    preflight: { timeout_seconds: 900, max_repairs: 0 },
  },
  "clean-example": {
    revision: "a99b2b41ab223a91bace2c86d43ff9bb9548c3a1",
    blurb: "依赖齐全，命令直接跑通。零修复一次成功。",
    expect: "succeeded",
  },
  "missing-dependency": {
    revision: "c6784189ca8faf81117fb1ecfabf4e5cc9f788fc",
    blurb: "requirements 漏了 PyYAML，装依赖即修好。",
    expect: "succeeded",
  },
  "cuda-only": {
    revision: "021d5fe2c0d9fd83d6c413916a209a4244eae03a",
    blurb: "默认抢 CUDA 设备，改设备变量走 CPU。",
    expect: "succeeded",
  },
  "deprecated-api": {
    revision: "093ecd35f5b1f242d02aa0411d05ec3a40f2ec7d",
    blurb: "调用已去掉的 verbose 参数，改调用点。",
    expect: "succeeded",
  },
  "hardcoded-path": {
    revision: "42214c2fe000932c0dadf6849806660167077c4f",
    blurb: "读写写死 /data 与 /tmp，改成相对路径。",
    expect: "succeeded",
  },
  "version-conflict": {
    revision: "accfa5c5f482385edae7678ad71818e979fa926c",
    blurb: "锁 numpy==2.0.0 但代码用旧 API，降版本。",
    expect: "succeeded",
  },
  "missing-private-data": {
    revision: "df424d0da23bc97dc4c45ae2e11bf487967625d2",
    blurb: "需要内部私有数据集，应判定不可复现。",
    expect: "failed",
  },
  "requires-gpu": {
    revision: "d000cdb9eb295de356b33eb20c7afb3593510f94",
    blurb: "确实需要 GPU，CPU 沙箱修不好，应停下。",
    expect: "failed",
  },
  "paper-pysindy": {
    revision: "c4421fcec275c8f4cc5c1e93bebb961b212067ae",
    blurb: "PySINDy 发布包的最小合成任务；不执行检出源码，也不是论文实验。",
    expect: "succeeded",
  },
  "paper-umap": {
    revision: "27f4b2a096a0341371e0707a7261b2e61f3c9151",
    blurb: "UMAP 发布包的合成数据试跑；不代表原论文实验复现。",
    expect: "succeeded",
  },
};

/** 后端不可用时的兜底列表（离线回放只用到 name / repo）。 */
export const FALLBACK_TARGETS = [
  { id: "paper-pysindy", repo_url: "https://github.com/dynamicslab/pysindy" },
  { id: "paper-umap", repo_url: "https://github.com/lmcinnes/umap" },
];
