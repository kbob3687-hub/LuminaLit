import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { targets as fetchTargets } from "../api";
import type { HealthDetails, Target } from "../types";
import { FALLBACK_TARGETS, TARGET_META } from "../targets";
import { HealthBanner } from "./HealthBanner";
import { RepairCaseEntry } from "./RepairCaseEntry";

/** 截图模式：关闭入场动画，避免无头渲染抓到半透明中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";
const CASE_PREVIEW = new URLSearchParams(window.location.search).get("case_preview") === "1";
const FADE_IN = (delay = 0) =>
  STILL
    ? { initial: false as const, animate: { opacity: 1, y: 0 } }
    : {
        initial: { opacity: 0, y: 14 },
        animate: { opacity: 1, y: 0 },
        transition: { duration: 0.6, delay, ease: [0.16, 1, 0.3, 1] as const },
      };

/**
 * 场景 1：选择靶场。
 *
 * 列表来自 `GET /api/targets`，不写死在前端——后端加了新靶场，这里自动出现。
 * revision 与预期结果来自 `src/targets.ts`（接口没回这两个字段）。
 *
 * 真实论文仓库（paper-pysindy / paper-umap）排在最后：它们要走真实 clone，
 * 比合成靶场慢一个量级。
 */
export interface PaperOption {
  id: string;
  name: string;
  arxiv?: string;
  desc?: string;
  tech?: string;
  /** 提交给 POST /api/runs 的 repo_url（契约 §4.1）。 */
  repo: string;
  /** 命中同一仓库下具体靶场的 commit。 */
  revision?: string;
  /** 设计上的终点：能修好 / 本来就不可复现。 */
  expect?: "succeeded" | "failed";
  preflight?: { timeout_seconds: number; max_repairs: number };
}

interface Props {
  onPick: (paper: PaperOption) => void;
  onViewRecord: (runId: string) => void;
  disabled?: boolean;
  busy?: boolean;
  /** 后端返回的错误（422 的 detail[0].msg / 429 queue_full 等），原样展示。 */
  error?: string | null;
  /** 离线回放模式：没有后端可提交，卡片下面要写明白。 */
  demo?: boolean;
  /** 观测者名字：不再写死，欢迎页直接用它。 */
  observer: string;
  onObserver: (name: string) => void;
  /** 评审稿 §11.3：宿主健康检查逐项结果；后端未实现时为 null。 */
  health?: HealthDetails | null;
  healthError?: string | null;
}

/** 后端列表 → 卡片数据。接口没回的字段从 TARGET_META 补。 */
function toOption(t: Target): PaperOption {
  const meta = TARGET_META[t.id];
  return {
    id: t.id,
    name: t.id,
    repo: t.repo_url,
    revision: meta?.revision,
    desc: meta?.blurb,
    expect: meta?.expect,
    preflight: meta?.preflight,
    tech: `python ${t.command.join(" ")} → ${t.expected_artifact ?? "无产物"}`,
  };
}

/** 真实仓库排后面，其余保持后端顺序。 */
function rank(o: PaperOption) {
  if (o.id === "paper-lorenz") return -1;
  return o.id.startsWith("paper-") ? 1 : 0;
}

const FALLBACK: PaperOption[] = FALLBACK_TARGETS.map((t) => ({
  id: t.id,
  name: t.id,
  repo: t.repo_url,
  revision: TARGET_META[t.id]?.revision,
  desc: TARGET_META[t.id]?.blurb,
  expect: TARGET_META[t.id]?.expect,
}));

export function SelectScene({ onPick, onViewRecord, disabled, busy, error, demo, observer, onObserver, health, healthError }: Props) {
  const [localError, setLocalError] = useState<string | null>(null);
  const [options, setOptions] = useState<PaperOption[] | null>(null);

  // 拉后端靶场清单；拿不到就退回内置两条真实论文靶场，不至于空白。
  useEffect(() => {
    let alive = true;
    fetchTargets()
      .then((list) => {
        if (!alive || !list.length) return;
        setOptions(list.map(toOption).sort((a, b) => rank(a) - rank(b)));
      })
      .catch(() => {
        /* 后端没起来：保持 null，下面用 FALLBACK */
      });
    return () => {
      alive = false;
    };
  }, []);

  const list = options ?? FALLBACK;
  const lorenz = options?.find(option => option.id === "paper-lorenz");
  const repair = options?.find(option => option.id === "paper-lorenz-repair");
  const shown = localError ?? error ?? null;

  /** 把输入框内容当作 repo_url 提交（契约 §4.1 的唯一必填字段）。 */
  const submit = () => {
    const el = document.getElementById("repo-input") as HTMLInputElement | null;
    const v = el?.value.trim() ?? "";
    if (!v) {
      setLocalError("请填写代码仓库地址，或选择下面的内置示例。");
      return;
    }
    if (/arxiv\.org/i.test(v)) {
      setLocalError(
        "这是论文页面，不是代码仓库。请填写论文对应的 HTTPS Git 仓库地址。",
      );
      return;
    }
    if (!/^https:\/\/[^/]+\/.+/.test(v)) {
      setLocalError("需要完整的 https 仓库地址，例如 https://github.com/owner/repo。");
      return;
    }
    setLocalError(null);
    onPick({ name: v.replace(/^https?:\/\//, "").replace(/\/+$/, ""), id: "custom", repo: v });
  };

  return (
    <main className="run-workspace h-full overflow-y-auto px-5 py-8 sm:px-8 sm:pt-[10vh]">
      <div className="mx-auto w-full max-w-[840px]">
        <motion.div
          {...FADE_IN(0)}
          className="mb-14 flex flex-wrap items-baseline justify-between gap-4 font-mono text-[11px] tracking-[.28em] text-accent uppercase"
        >
          <span className="tracking-normal normal-case">LuminaLit · 科研代码复现与交接</span>

          <div className="flex items-baseline gap-5 tracking-[.08em]">
            {/* 观测者：欢迎页显示的名字，跟着浏览器走，不写死一个人 */}
            <label className="flex items-baseline gap-2 text-[10.5px] text-ink-muted normal-case">
              观测者
              <input
                value={observer}
                onChange={(e) => onObserver(e.target.value)}
                placeholder="你的名字"
                maxLength={16}
                spellCheck={false}
                aria-label="观测者名字，显示在欢迎页"
                title="填自己的名字，欢迎页会显示「欢迎您，XXX」"
                className="w-[126px] border-b border-rule bg-transparent pb-0.5 text-center font-mono text-[11.5px] tracking-normal text-ink normal-case outline-none transition-colors placeholder:text-ink-muted/60 focus:border-accent"
              />
            </label>

            <span className={demo ? "text-warn" : "text-ink-muted"}>
              {demo ? "离线回放 · 未连后端" : options ? `${list.length} 个内置靶场` : "连接后端…"}
            </span>
          </div>
        </motion.div>

        <motion.h1
          {...FADE_IN(0.06)}
          className="mb-5 max-w-[17ch] text-[clamp(36px,4.5vw,58px)] leading-[1.2] font-semibold tracking-[-.035em]"
        >
          <span className="block">修好的科研代码，</span><span className="block">换个环境还能跑吗？</span>
        </motion.h1>

        <motion.p {...FADE_IN(0.12)} className="mb-10 max-w-[56ch] text-[14px] leading-[1.9] text-ink-mid">
          接手论文仓库，查看运行问题与修复记录，按固定标准检查结果，再把复跑材料交给同学。
        </motion.p>

        <RepairCaseEntry onView={onViewRecord} onRetry={repair && !disabled ? () => onPick(repair) : undefined} demo={demo} />

        {lorenz && <section aria-label="推荐科研实验" className="mb-9 rounded-xl border border-rule-hi bg-surface px-5 py-5 sm:px-6">
          <h2 className="text-lg font-semibold text-ink">先复跑一个具体实验</h2>
          <p className="mt-2 max-w-[65ch] text-sm leading-7 text-ink-mid">SINDy 从数据中识别 Lorenz 方程。执行固定版本的仓库源码，检查三个方程的系数，再创建全新容器复跑。</p>
          <p className="mt-2 text-xs leading-6 text-ink-muted">低噪声系数识别子实验；数据本地生成，仅需 CPU，固定参数、不调用模型。首次下载与安装可能较慢。</p>
          <button type="button" onClick={() => onPick(lorenz)} disabled={disabled} className="mt-4 cursor-pointer rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-white hover:brightness-110 disabled:opacity-50">{demo ? "查看离线回放" : "查看 Lorenz 实验与验收标准"}</button>
        </section>}

        {CASE_PREVIEW && <p role="status" className="mb-8 text-sm leading-7 text-ink-mid">当前为科研案例预览。查看历史记录不会启动任务；重新运行需先查看并确认执行计划。</p>}
        {!CASE_PREVIEW && <motion.div {...FADE_IN(0.16)} className="mb-8">
          <label htmlFor="repo-input" className="mb-3 block text-base font-medium">运行自己的仓库</label>
          <form onSubmit={(event) => { event.preventDefault(); submit(); }}
            className="flex flex-col gap-3 rounded-xl border border-accent/30 bg-surface/80 p-2 shadow-[0_0_45px_-18px_rgb(108_143_255_/_0.35)] transition-colors focus-within:border-accent sm:flex-row sm:items-stretch">
            <input
              id="repo-input"
              placeholder="https://github.com/owner/repo"
              aria-describedby="repo-help"
              spellCheck={false}
              disabled={disabled}
              onInput={() => setLocalError(null)}
              className="min-w-0 flex-1 border-0 bg-transparent px-3 py-[13px] font-mono text-[13px] text-ink outline-none placeholder:text-ink-muted disabled:opacity-50"
            />
            <button type="submit" disabled={disabled}
              className="shrink-0 cursor-pointer rounded-lg bg-linear-to-br from-accent to-[#5272e7] px-6 py-[10px] text-[13.5px] font-semibold text-white transition-colors hover:brightness-110 disabled:opacity-50">
              {demo ? "查看离线回放" : "开始探索运行"}
            </button>
          </form>
          <p id="repo-help" className="mt-3 max-w-[80ch] text-xs leading-6 text-ink-mid">
            {demo ? "当前为离线演示：不会下载你填写的仓库，也不会创建真实任务。" : "自定义仓库暂不提供运行前体检；提交后立即下载代码，由模型分析计划并尝试运行，可能发送代码片段并产生模型费用。"}
          </p>
        </motion.div>}

        {shown && <p role="alert" className="mb-5 rounded-lg border border-reject/40 bg-reject/[.07] px-4 py-3 text-sm leading-6 text-ink">{shown}</p>}
        {busy && <p role="status" className="mb-5 text-sm text-ink-mid">正在提交请求，请稍候…</p>}

        <details className="mb-6 border-y border-rule py-5">
          <summary className="cursor-pointer text-sm font-medium text-ink-mid">先用内置示例了解流程 <span className="ml-2 font-mono text-xs text-ink-muted">{list.length} 个案例</span></summary>
        <div className="my-4">
          <p className="mt-2 text-xs leading-6 text-ink-mid">
            {demo ? "选择示例查看预录过程，不会执行代码。" : "点击示例先查看静态体检，再确认执行。它们是小型运行案例，不代表完整论文复现。"}
          </p>
        </div>
        <div className="grid gap-2.5 sm:grid-cols-2">
          {list.map((p, i) => (
            <TargetCard key={p.id} option={p} index={i} onPick={onPick} disabled={disabled} />
          ))}
        </div>
        </details>

        <details className="mb-4 text-xs text-ink-muted">
          <summary className="mb-4 cursor-pointer">运行条件与服务状态</summary>
          <HealthBanner details={health ?? null} error={healthError ?? null} />
        </details>

      </div>
    </main>
  );
}

function TargetCard({
  option,
  index,
  onPick,
  disabled,
}: {
  option: PaperOption;
  index: number;
  onPick: Props["onPick"];
  disabled?: boolean;
}) {
  return (
    <motion.button
      type="button"
      disabled={disabled}
      {...FADE_IN(0.2 + Math.min(index, 8) * 0.035)}
      whileHover={{ y: -3 }}
      whileTap={{ scale: 0.99 }}
      onClick={() => {
        if (!option.repo) {
          document.getElementById("repo-input")?.focus();
          return;
        }
        onPick(option);
      }}
      className="group relative cursor-pointer overflow-hidden rounded-[9px] border border-rule bg-surface-hi/50 px-[17px] py-4 text-left transition-colors hover:border-accent/55 disabled:opacity-50"
    >

      <span className="mb-2 flex items-center gap-2 font-mono text-[9.5px] tracking-[.14em]">
        <span
          className={
            option.expect === "failed"
              ? "text-warn/85"
              : option.expect === "succeeded"
                ? "text-pass/85"
                : "text-ink-muted"
          }
        >
          {option.expect === "failed" ? "缺少条件的示例" : option.expect === "succeeded" ? "可运行的示例" : "受维护示例"}
        </span>
      </span>

      <span className="mb-1.5 block truncate font-mono text-[13.5px] font-medium tracking-[-.01em] text-ink">
        {option.name}
      </span>

      <span className="block text-[11.5px] leading-[1.65] text-ink-muted">
        {option.desc ?? "—"}
      </span>
    </motion.button>
  );
}
