import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { SelectScene } from "./components/SelectScene";
import { WelcomeScene } from "./components/WelcomeScene";
import { RunScene, type ActionRow } from "./components/RunScene";
import { PARTICLE_COLORS, ParticleField, type ParticleBurst } from "./components/ParticleField";
import { useRunPolling } from "./hooks/useRunPolling";
import type { RunEvent, RunStatus } from "./types";

type Scene = "select" | "welcome" | "run";

/** 截图模式下把所有过渡时长压成 0，避免无头渲染抓到半透明中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";

const DEMO_ROWS: ActionRow[] = [
  { seq: 1, ts: "00:50.1", kind: "call", name: "read_file", target: "src/model.py", result: "ok" },
  { seq: 2, ts: "00:50.9", kind: "call", name: "search_text", target: '"import" · src/', result: "7 matches" },
  { seq: 3, ts: "00:51.4", kind: "call", name: "read_file", target: "requirements.txt", result: "文件不存在" },
  {
    seq: 4,
    ts: "00:52.3",
    kind: "reject",
    name: "read_file",
    target: "../../etc/passwd",
    result: "path_escape",
    blocked: true,
    reason: "路径越出 workspace 边界，已拒绝且未产生任何副作用",
  },
  { seq: 5, ts: "00:52.8", kind: "call", name: "replace_exact", target: "src/model.py:42", result: "1 occurrence" },
  {
    seq: 6,
    ts: "00:53.1",
    kind: "reject",
    name: "install_requirement",
    target: "numpy; rm -rf /",
    result: "requirement_not_allowed",
    blocked: true,
    reason: "依赖字符串含命令分隔符，拒绝后未打开网络",
  },
  { seq: 7, ts: "00:53.9", kind: "ok", name: "install_requirement", target: "numpy==2.0.0", result: "exit 0" },
  { seq: 8, ts: "00:54.6", kind: "finish", name: "finish_repair", target: '"补上 numpy 依赖"', result: "—" },
];

const DEMO_STATUS: RunStatus = {
  run_id: "a3f29c8e1b4d4f2e",
  state: "repairing",
  result: null,
};

export default function App() {
  const [scene, setScene] = useState<Scene>("select");
  const [paper, setPaper] = useState({ name: "PySINDy", id: "paper-pysindy" });
  const [bursts, setBursts] = useState<ParticleBurst[]>([]);
  const [runId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const timer = useRef<number | null>(null);

  /** 截图/演示直链：?scene=run / ?scene=welcome，并可用 ?still=1 关闭动画。 */
  const q = new URLSearchParams(window.location.search);
  const initialScene = (q.get("scene") as Scene | null) ?? "select";

  useEffect(() => {
    if (initialScene !== "select") setScene(initialScene);
  }, [initialScene]);

  /** 演示模式：默认开启，接后端时设 VITE_DEMO=0。 */
  const demo = (import.meta.env.VITE_DEMO ?? "1") !== "0";

  const { status, events } = useRunPolling(demo ? null : runId, {
    mock: demo ? { status: DEMO_STATUS, events: [] } : undefined,
  });

  // 统一触发粒子爆发
  const fire = useCallback((x: number, y: number, color: string, n = 30) => {
    setBursts([{ x, y, color, count: n, speed: 2.6, life: 70 }]);
  }, []);

  // 进入运行场景后：拦截行持续喷粒子 + 当前阶段冒粒子
  useEffect(() => {
    if (scene !== "run") return;
    const blocked = () => {
      const el = document.querySelector("[data-blocked='1']");
      if (!el) return;
      const r = el.getBoundingClientRect();
      fire(r.left + 110, r.top + r.height / 2, PARTICLE_COLORS.reject, 32);
    };
    const t1 = window.setTimeout(blocked, 900);
    const t2 = window.setInterval(blocked, 1500);
    return () => {
      window.clearTimeout(t1);
      window.clearInterval(t2);
    };
  }, [scene, fire]);

  // 欢迎 → 运行
  const pick = (p: { name: string; id: string }) => {
    setPaper(p);
    setScene("welcome");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setScene("run");
      fire(window.innerWidth / 2, window.innerHeight / 2, PARTICLE_COLORS.accent, 70);
    }, 3050);
  };

  // 把真实事件映射成动作行（接后端时用）
  const rows = useMemo(() => {
    if (demo) return DEMO_ROWS;
    return events
      .filter((e: RunEvent) => e.kind !== "diagnosis" && e.kind !== "repair_round")
      .map((e) => {
        const blocked = /path_escape|not_allowed|not_unique|not_found|invalid_argument/.test(
          e.kind + e.message,
        );
        return {
          seq: e.sequence,
          ts: new Date(e.timestamp).toISOString().slice(14, 21),
          kind: blocked ? "reject" : ("call" as const),
          name: e.kind,
          target: String(e.data?.path ?? e.data?.requirement ?? ""),
          result: e.message.slice(0, 40),
          blocked,
          reason: blocked ? e.message : undefined,
        } satisfies ActionRow & { seq: number };
      })
      .slice(-60);
  }, [demo, events]);

  return (
    <div className="relative h-full overflow-hidden">
      {/* 景深层 */}
      <GridLayer />
      <ParticleField bursts={bursts} />
      <Spotlight />
      <Vignette />

      <AnimatePresence mode="wait">
        {scene === "select" && (
          <SceneShell key="select">
            <SelectScene onPick={pick} />
          </SceneShell>
        )}

        {scene === "welcome" && (
          <SceneShell key="welcome">
            <WelcomeScene paperName={paper.name} userName="嘉豪" />
          </SceneShell>
        )}

        {scene === "run" && (
          <SceneShell key="run">
            <RunScene
              status={status}
              rows={rows}
              cancelling={cancelling}
              onCancel={async () => {
                if (!runId) return;
                setCancelling(true);
                const { cancelRun } = await import("./api");
                try {
                  await cancelRun(runId);
                } catch {
                  /* 失败也不阻塞 UI */
                } finally {
                  setCancelling(false);
                }
              }}
            />
          </SceneShell>
        )}
      </AnimatePresence>
    </div>
  );
}

/** 场景外壳：统一进出场过渡。 */
function SceneShell({ children }: { children: React.ReactNode }) {
  return (
    <motion.section
      className="absolute inset-0 z-10"
      initial={STILL ? false : { opacity: 0, filter: "blur(8px)", scale: 1.03 }}
      animate={{ opacity: 1, filter: "blur(0px)", scale: 1 }}
      exit={{ opacity: 0, filter: "blur(8px)", scale: 1.03 }}
      transition={{ duration: STILL ? 0 : 0.55, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.section>
  );
}

/** 网格底纹：随鼠标反向轻移，制造视差。 */
function GridLayer() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-[-10%] z-0"
      style={{
        backgroundImage:
          "linear-gradient(rgb(108 143 255 / .055) 1px, transparent 1px), linear-gradient(90deg, rgb(108 143 255 / .055) 1px, transparent 1px)",
        backgroundSize: "56px 56px",
        maskImage:
          "radial-gradient(ellipse 70% 60% at 50% 45%, #000 0%, transparent 100%)",
        transform:
          "translate3d(calc(var(--mx, 0) * -14px), calc(var(--my, 0) * -14px), 0)",
        transition: "transform .6s cubic-bezier(.16,1,.3,1)",
      }}
    />
  );
}

/** 鼠标光池：空间感的主要来源。 */
function Spotlight() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 z-[2]"
      style={{
        background:
          "radial-gradient(520px circle at var(--cx, 50%) var(--cy, 50%), rgb(108 143 255 / .10), rgb(108 143 255 / .04) 38%, transparent 68%)",
      }}
    />
  );
}

/** 暗角：把视线压到中间。 */
function Vignette() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 z-[3]"
      style={{
        background:
          "radial-gradient(ellipse 90% 75% at 50% 45%, transparent 40%, rgb(0 0 0 / .65) 100%)",
      }}
    />
  );
}
