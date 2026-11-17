import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { targets as fetchTargets } from "../api";
import type { Target } from "../types";
import { FALLBACK_TARGETS, TARGET_META } from "../targets";

/** 截图模式：关闭入场动画，避免无头渲染抓到半透明中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";
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
}

interface Props {
  onPick: (paper: PaperOption) => void;
  disabled?: boolean;
  /** 后端返回的错误（422 的 detail[0].msg / 429 queue_full 等），原样展示。 */
  error?: string | null;
  /** 离线回放模式：没有后端可提交，卡片下面要写明白。 */
  demo?: boolean;
  /** 观测者名字：不再写死，欢迎页直接用它。 */
  observer: string;
  onObserver: (name: string) => void;
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
    tech: `python ${t.command.join(" ")} → ${t.expected_artifact ?? "无产物"}`,
  };
}

/** 真实仓库排后面，其余保持后端顺序。 */
function rank(o: PaperOption) {
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

export function SelectScene({ onPick, disabled, error, demo, observer, onObserver }: Props) {
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
  const shown = localError ?? error ?? null;

  /** 把输入框内容当作 repo_url 提交（契约 §4.1 的唯一必填字段）。 */
  const submit = () => {
    const el = document.getElementById("repo-input") as HTMLInputElement | null;
    const v = el?.value.trim() ?? "";
    if (!v) {
      setLocalError("先填一个 GitHub 仓库地址，或直接点上面的靶场卡片。");
      return;
    }
    if (/arxiv\.org/i.test(v)) {
      setLocalError(
        "arXiv 链接不是 Git 仓库：POST /api/runs 只接受 repo_url，把 arXiv 地址发过去会以 clone_failed 失败（已实测）。请填论文对应的 GitHub 仓库地址，例如 https://github.com/dynamicslab/pysindy。",
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
    <div className="h-full overflow-y-auto px-6 py-10">
      <div className="mx-auto w-full max-w-[1020px]">
        <motion.div
          {...FADE_IN(0)}
          className="mb-6 flex items-baseline justify-between font-mono text-[11px] tracking-[.28em] text-accent uppercase"
        >
          <span>PaperHarness</span>

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
          className="mb-3 text-[clamp(30px,3.6vw,44px)] leading-[1.12] font-semibold tracking-[-.02em]"
        >
          选一个靶场，
          <span className="text-accent [text-shadow:0_0_34px_rgb(108_143_255_/_0.55)]">
            看它被跑通
          </span>
          或判定不可复现
        </motion.h1>

        <motion.p {...FADE_IN(0.12)} className="mb-8 max-w-[52ch] text-[14px] leading-[1.7] text-ink-mid">
          整个复现过程在隔离容器里执行。AI 提出的每一个动作都要过校验门岗，
          越界的请求会被当场拦下并留下记录。
        </motion.p>

        <motion.div
          {...FADE_IN(0.16)}
          className="mb-3 font-mono text-[10.5px] tracking-[.16em] text-ink-muted uppercase"
        >
          内置靶场 · 点一张直接开跑
        </motion.div>

        <div className="mb-8 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {list.map((p, i) => (
            <TargetCard key={p.id} option={p} index={i} onPick={onPick} disabled={disabled} />
          ))}
        </div>

        <motion.div
          {...FADE_IN(0.3)}
          className="mb-3 font-mono text-[10.5px] tracking-[.16em] text-ink-muted uppercase"
        >
          或直接输入仓库地址
        </motion.div>

        <motion.div
          {...FADE_IN(0.34)}
          className="flex items-stretch gap-3 border-b border-rule transition-colors focus-within:border-accent"
        >
          <input
            id="repo-input"
            placeholder="https://github.com/owner/repo"
            spellCheck={false}
            disabled={disabled}
            onInput={() => setLocalError(null)}
            className="flex-1 border-0 bg-transparent py-[13px] font-mono text-[13px] text-ink outline-none placeholder:text-ink-muted disabled:opacity-50"
          />
          <button
            disabled={disabled}
            onClick={submit}
            className="my-2 shrink-0 cursor-pointer rounded-[7px] border border-transparent bg-gradient-to-br from-[#7c9bff] to-[#5a7cf0] px-6 py-[10px] text-[13.5px] font-semibold text-void shadow-[0_8px_26px_-8px_rgb(108_143_255_/_0.8)] transition-transform hover:-translate-y-0.5 disabled:opacity-50"
          >
            开始复现
          </button>
        </motion.div>

        <motion.div
          {...FADE_IN(0.38)}
          className="mt-3 font-mono text-[10.5px] leading-[1.7] text-ink-muted"
        >
          提交的是 <code className="text-ink-mid">repo_url</code> +{" "}
          <code className="text-ink-mid">revision</code>：命中内置靶场时运行计划来自靶场清单，
          不依赖模型；自定义仓库则由模型现场生成计划。
        </motion.div>

        {shown && (
          <motion.div
            {...FADE_IN(0.4)}
            role="alert"
            className="mt-3.5 rounded-[7px] border border-[rgb(255_96_80_/_0.42)] border-l-[3px] border-l-[rgb(255_92_71)] bg-[rgb(255_96_80_/_0.09)] px-3.5 py-3 font-mono text-[11.5px] leading-[1.8] text-[#ffd9d5]"
          >
            {shown}
          </motion.div>
        )}
      </div>
    </div>
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
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        e.currentTarget.style.setProperty("--px", `${((e.clientX - r.left) / r.width) * 100}%`);
        e.currentTarget.style.setProperty("--py", `${((e.clientY - r.top) / r.height) * 100}%`);
      }}
      className="group relative cursor-pointer overflow-hidden rounded-[9px] border border-rule bg-gradient-to-br from-white/[.045] to-white/[.012] px-[17px] py-4 text-left transition-colors hover:border-accent/55 disabled:opacity-50"
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{
          background:
            "radial-gradient(280px circle at var(--px, 50%) var(--py, 50%), rgb(108 143 255 / .16), transparent 62%)",
        }}
      />

      <span className="mb-2 flex items-center gap-2 font-mono text-[9.5px] tracking-[.14em]">
        <span className="text-accent/80">{String(index + 1).padStart(2, "0")}</span>
        <span className="text-ink-muted">·</span>
        <span
          className={
            option.expect === "failed"
              ? "text-warn/85"
              : option.expect === "succeeded"
                ? "text-pass/85"
                : "text-ink-muted"
          }
        >
          {option.expect === "failed" ? "预期不可复现" : option.expect === "succeeded" ? "预期跑通" : "—"}
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
