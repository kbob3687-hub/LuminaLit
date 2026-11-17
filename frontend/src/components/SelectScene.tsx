import { useState } from "react";
import { motion } from "motion/react";

/** 截图模式：关闭入场动画，避免无头渲染抓到半透明中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";
const FADE_IN = (delay = 0) =>
  STILL ? { initial: false as const, animate: { opacity: 1, y: 0 } } : { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.6, delay, ease: [0.16, 1, 0.3, 1] as const } };

/**
 * 场景 1：选择论文。
 *
 * 三级层次：eyebrow（定位）→ 主标题（主张）→ 卡片（可操作对象）。
 * 卡片 hover 有 3D 倾斜 + 内部光斑跟随（不跟随就用 CSS 变量，避免每帧 setState）。
 */
export interface PaperOption {
  id: string;
  name: string;
  arxiv: string;
  desc: string;
  tech: string;
  /** 提交给 POST /api/runs 的 repo_url（契约 §4.1 唯一必填字段）。 */
  repo: string;
}

const PAPERS: PaperOption[] = [
  {
    id: "paper-pysindy",
    name: "PySINDy",
    arxiv: "arXiv:2004.08424",
    desc: "稀疏识别动力学方程",
    tech: "x' = -2.000x",
    repo: "https://github.com/dynamicslab/pysindy",
  },
  {
    id: "paper-umap",
    name: "UMAP",
    arxiv: "arXiv:1802.03426",
    desc: "200×10 降维至 2 维",
    tech: "ARI ≥ 0.999",
    repo: "https://github.com/lmcinnes/umap",
  },
  {
    id: "custom",
    name: "自定义仓库",
    arxiv: "任意公开源",
    desc: "输入 GitHub 地址",
    tech: "自动生成计划",
    repo: "",
  },
];

interface Props {
  onPick: (paper: { name: string; id: string; repo: string }) => void;
  disabled?: boolean;
  /** 后端返回的错误（422 的 detail[0].msg / 429 queue_full 等），原样展示。 */
  error?: string | null;
}

export function SelectScene({ onPick, disabled, error }: Props) {
  const [localError, setLocalError] = useState<string | null>(null);
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
    <div className="flex h-full items-center justify-center px-6">
      <div
        className="w-full max-w-[900px]"
        style={{
          transform:
            "translate3d(calc(var(--mx, 0) * -8px), calc(var(--my, 0) * -8px), 0)",
          transition: "transform .5s cubic-bezier(.16,1,.3,1)",
        }}
      >
        <motion.div {...FADE_IN(0)} className="mb-7 font-mono text-[11px] tracking-[.32em] text-accent uppercase">
          PaperHarness
        </motion.div>

        <motion.h1 {...FADE_IN(0.06)} className="mb-3.5 text-[clamp(38px,5.2vw,64px)] leading-[1.06] font-semibold tracking-[-.02em]">
          选择一篇论文
          <br />
          看它被
          <span className="text-accent [text-shadow:0_0_34px_rgb(108_143_255_/_0.55)]">
            跑通
          </span>
          或
          <span className="text-accent [text-shadow:0_0_34px_rgb(108_143_255_/_0.55)]">
            修好
          </span>
        </motion.h1>

        <motion.p {...FADE_IN(0.14)} className="mb-11 max-w-[46ch] text-[15px] leading-[1.7] text-ink-mid">
          整个复现过程在隔离容器里执行。AI 提出的每一个动作都要过校验门岗，
          越界的请求会被当场拦下并留下记录。
        </motion.p>

        <motion.div {...FADE_IN(0.22)} className="mb-3.5 font-mono text-[11px] tracking-[.16em] text-ink-muted uppercase">
          内置靶场
        </motion.div>

        <div className="mb-11 grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {PAPERS.map((p, i) => (
            <PaperCard key={p.id} paper={p} index={i} onPick={onPick} disabled={disabled} />
          ))}
        </div>

        <motion.div {...FADE_IN(0.4)} className="mb-3.5 font-mono text-[11px] tracking-[.16em] text-ink-muted uppercase">
          或直接输入
        </motion.div>

        <motion.div {...FADE_IN(0.44)} className="flex items-stretch gap-3 border-b border-rule transition-colors focus-within:border-accent">
          <input
            id="repo-input"
            placeholder="https://github.com/owner/repo"
            spellCheck={false}
            disabled={disabled}
            onInput={() => setLocalError(null)}
            className="flex-1 border-0 bg-transparent py-[15px] font-mono text-[13px] text-ink outline-none placeholder:text-ink-muted disabled:opacity-50"
          />
          <button
            disabled={disabled}
            onClick={submit}
            className="my-2 shrink-0 cursor-pointer rounded-[7px] border border-transparent bg-gradient-to-br from-[#7c9bff] to-[#5a7cf0] px-6 py-[11px] text-[13.5px] font-semibold text-void shadow-[0_8px_26px_-8px_rgb(108_143_255_/_0.8)] transition-transform hover:-translate-y-0.5 disabled:opacity-50"
          >
            开始复现
          </button>
        </motion.div>

        <motion.div {...FADE_IN(0.48)} className="mt-3 font-mono text-[11px] leading-[1.7] text-ink-muted">
          提交的是 <code className="text-ink-mid">repo_url</code>：后端只认 Git 仓库地址，论文用对应仓库代替。
        </motion.div>

        {shown && (
          <motion.div
            {...FADE_IN(0.5)}
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

function PaperCard({
  paper,
  index,
  onPick,
  disabled,
}: {
  paper: PaperOption;
  index: number;
  onPick: Props["onPick"];
  disabled?: boolean;
}) {
  return (
    <motion.button
      type="button"
      disabled={disabled}
      {...FADE_IN(0.26 + index * 0.07)}
      whileHover={{ y: -5, scale: 1.018 }}
      whileTap={{ scale: 0.985 }}
      onClick={() => {
        // 「自定义仓库」没有固定 repo_url：聚焦输入框，由用户给具体地址
        if (!paper.repo) {
          document.getElementById("repo-input")?.focus();
          return;
        }
        onPick({ name: paper.name, id: paper.id, repo: paper.repo });
      }}
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        e.currentTarget.style.setProperty("--px", `${((e.clientX - r.left) / r.width) * 100}%`);
        e.currentTarget.style.setProperty("--py", `${((e.clientY - r.top) / r.height) * 100}%`);
      }}
      className="group relative cursor-pointer overflow-hidden rounded-[10px] border border-rule bg-gradient-to-br from-white/[.045] to-white/[.012] px-[22px] py-5 text-left transition-colors hover:border-accent/55 disabled:opacity-50"
      style={{
        boxShadow: "0 0 0 0 transparent",
      }}
    >
      {/* 内部光斑跟随 */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{
          background:
            "radial-gradient(320px circle at var(--px, 50%) var(--py, 50%), rgb(108 143 255 / .16), transparent 62%)",
        }}
      />
      <span className="mb-2.5 block font-mono text-[10px] tracking-[.16em] text-accent/80">
        {String(index + 1).padStart(2, "0")} · {paper.arxiv}
      </span>
      <span className="mb-1.5 block text-base font-semibold tracking-[-.01em]">
        {paper.name}
      </span>
      <span className="block font-mono text-[10.5px] leading-[1.7] text-ink-muted">
        {paper.desc}
        <br />
        {paper.tech}
      </span>
    </motion.button>
  );
}
