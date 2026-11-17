import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { SelectScene } from "./components/SelectScene";
import { WelcomeScene } from "./components/WelcomeScene";
import { RunScene } from "./components/RunScene";
import { PARTICLE_COLORS, ParticleField, type ParticleBurst } from "./components/ParticleField";
import { useRunPolling } from "./hooks/useRunPolling";
import { useDemoRun } from "./hooks/useDemoRun";
import { createRun, cancelRun, ApiError } from "./api";

type Scene = "select" | "welcome" | "run";

/** 截图模式下把所有过渡时长压成 0，避免无头渲染抓到半透明中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";

export default function App() {
  const [scene, setScene] = useState<Scene>("select");
  const [paper, setPaper] = useState({ name: "PySINDy", id: "paper-pysindy" });
  const [bursts, setBursts] = useState<ParticleBurst[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [pickError, setPickError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const timer = useRef<number | null>(null);

  /** 截图/演示直链：?scene=run / ?scene=welcome，并可用 ?still=1 关闭动画。 */
  const q = new URLSearchParams(window.location.search);
  const initialScene = (q.get("scene") as Scene | null) ?? "select";

  useEffect(() => {
    if (initialScene !== "select") setScene(initialScene);
  }, [initialScene]);

  /**
   * 演示模式：**默认关闭**，只在 `?demo=1` 或 `VITE_DEMO=1` 时回放脚本。
   * 之前默认开着，界面上所有"进度"都是假数据——状态条跟后端毫无关系，
   * 一进场就把阶段全点亮。真实后端是唯一事实来源。
   */
  const demo = q.get("demo") === "1" || import.meta.env.VITE_DEMO === "1";

  const live = useRunPolling(demo ? null : runId);
  const replay = useDemoRun(demo, Number(q.get("t") ?? 0));
  const status = demo ? replay.status : live.status;
  const events = demo ? replay.events : live.events;

  // 统一触发粒子爆发
  const fire = useCallback((x: number, y: number, color: string, n = 30) => {
    setBursts([{ x, y, color, count: n, speed: 2.6, life: 70 }]);
  }, []);

  // 进入运行场景后：拦截行持续喷粒子
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

  // 选论文 / 仓库 → 真接口创建 run → 欢迎页 → 运行页
  const pick = async (p: { name: string; id: string; repo: string }) => {
    setPaper({ name: p.name, id: p.id });
    setPickError(null);

    if (!demo) {
      // 契约 §4.1：POST /api/runs 只接受 repo_url，202 返回 run_id
      setCreating(true);
      try {
        const res = await createRun({ repo_url: p.repo });
        setRunId(res.run_id);
      } catch (err) {
        const e = err as ApiError;
        setPickError(
          e?.status === 429
            ? "队列已满（queue_full），稍后重试。"
            : e?.status === 422
              ? `输入不合法：${e.message}`
              : `创建任务失败：${e?.message ?? String(err)}`,
        );
        setCreating(false);
        return;
      }
      setCreating(false);
    }

    setScene("welcome");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setScene("run");
      fire(window.innerWidth / 2, window.innerHeight / 2, PARTICLE_COLORS.accent, 70);
    }, 3050);
  };

  // 动作行、阶段条、回环全部在 RunScene 里由事件推导（runModel.ts）
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
            <SelectScene onPick={pick} disabled={creating} error={pickError} />
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
              events={events}
              cancelling={cancelling}
              onCancel={async () => {
                if (!runId) return;
                setCancelling(true);
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
