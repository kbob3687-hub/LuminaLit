import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { SelectScene } from "./components/SelectScene";
import { WelcomeScene } from "./components/WelcomeScene";
import { RunScene } from "./components/RunScene";
import { PARTICLE_COLORS, ParticleField, type ParticleBurst } from "./components/ParticleField";
import { useRunPolling } from "./hooks/useRunPolling";
import { useDemoRun } from "./hooks/useDemoRun";
import { createRun, cancelRun, ApiError } from "./api";
import { finiteNonNegative, parseScene, type Scene } from "./deepLink";

/** 截图模式下把所有过渡时长压成 0，避免无头渲染抓到半透明中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";

/**
 * 观测者名字：存在浏览器本地，不再写死一个人名。
 *
 * 之前 WelcomeScene 直接 `<WelcomeScene userName="嘉豪" />`，换个人用就变成
 * 别人的名字。现在留空就用「观测者」兜底，在选靶场页可以改成自己的名字。
 */
const NAME_KEY = "ph.observer";
const FALLBACK_NAME = "观测者";

function readName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

export default function App() {
  const [scene, setScene] = useState<Scene>("select");
  /** 靶场名不再是硬编码的 "PySINDy"：默认空，由用户点的那张卡决定。 */
  const [paper, setPaper] = useState({ name: "", id: "" });
  const [observer, setObserver] = useState(readName);
  const [bursts, setBursts] = useState<ParticleBurst[]>([]);
  const [runId, setRunId] = useState<string | null>(() => {
    // ?run=<id> 直接盯一个已存在的 run（调试/答辩演示用，不经过选择页）
    return new URLSearchParams(window.location.search).get("run");
  });
  const [creating, setCreating] = useState(false);
  const [pickError, setPickError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const timer = useRef<number | null>(null);

  /**
   * 截图/演示直链：?scene=run / ?scene=welcome，?run=<id> 直接盯一个 run。
   *
   * scene 走白名单（见 deepLink.ts），非法值退回 select，不会落进三个渲染
   * 分支之外的空隙。另外 `?scene=run` 但没带 `?run=` 时，live 模式会
   * `useRunPolling(null)` → 一个请求都不发 → 永久停在「连接中 · 等待后端」；
   * 除非是离线回放（有本地时间轴），否则退回选择页。
   */
  const q = new URLSearchParams(window.location.search);
  const rawScene = parseScene(q, Boolean(q.get("run")));
  const wantDemoLink = q.get("demo") === "1" || import.meta.env.VITE_DEMO === "1";
  const initialScene: Scene =
    rawScene === "run" && !q.get("run") && !wantDemoLink ? "select" : rawScene;

  useEffect(() => {
    if (initialScene !== "select") setScene(initialScene);
  }, [initialScene]);

  /**
   * ?scene=welcome 是截图直链，它没经过 pick()，所以下面那个 3050ms 的
   * 「欢迎 → 运行」定时器不会被设置，页面会永久停在欢迎页。这里补一个：
   * 有 runId 就进运行页，没有（离线回放）也让时间轴跑起来。
   */
  useEffect(() => {
    if (initialScene !== "welcome") return;
    const t = window.setTimeout(
      () => setScene(q.get("run") || wantDemoLink ? "run" : "select"),
      STILL ? 0 : 3050,
    );
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * 数据来源只有两种，且界面上必须能看出来是哪种：
   *
   *   live   —— 后端活着：状态和事件全部来自 GET /api/runs/{id}，前端不改写。
   *   replay —— 离线回放：后端没起来时走这条，否则本地起前端点进去只会
   *             看到"连接中 等待后端"的空壳。回放本身也是逐条事件推进，
   *             进场只有 queued 亮，不预置绿灯。
   *
   * 探测优先于配置：启动时打一次 /api/health（1.5s 超时），
   * 通了就是 live，不通就回放。这样不用逼用户记 ?live=1 这种参数。
   * `?demo=1` / `?live=1` 仍然可以强行覆盖。
   *
   * 注意不要用 VITE_API_BASE_URL 直连 8000：后端 app/api.py 挂了
   * CORSMiddleware，默认只允许 5173/4173 及 PAPERHARNESS_CORS_ORIGINS 配置的
   * origin；部署到白名单外域名时直连会被拦。留空走 vite.config.ts 的同源
   * proxy 最稳，不用赌部署域名在不在白名单里。
   */
  const apiBase = import.meta.env.VITE_API_BASE_URL as string | undefined;
  const wantReplay = q.get("demo") === "1" || import.meta.env.VITE_DEMO === "1";
  const wantLive = q.get("live") === "1" || Boolean(q.get("run"));

  /** null = 还在探测 */
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    const ctrl = new AbortController();
    const t = window.setTimeout(() => ctrl.abort(), 1500);
    fetch(`${apiBase ?? ""}/api/health`, { signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((b: { status?: string }) => {
        if (alive) setHealthy(b?.status === "ok");
      })
      .catch(() => {
        // 探测失败不是错误，只是"后端没起来"，回落到回放即可
        if (alive) setHealthy(false);
      })
      .finally(() => window.clearTimeout(t));
    return () => {
      alive = false;
      window.clearTimeout(t);
      ctrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mode: "live" | "replay" = wantReplay
    ? "replay"
    : wantLive
      ? "live"
      : healthy === true
        ? "live"
        : "replay";
  const demo = mode === "replay";

  const live = useRunPolling(mode === "live" ? runId : null);
  // ?t=abc → Number(...) 是 NaN，useDemoRun 里 `acc > seekMs` 恒 false，
  // pre 铺满整个 SCRIPT 后就不再播放。非数字一律当作 0。
  const replay = useDemoRun(demo, finiteNonNegative(q.get("t")));
  const status = demo ? replay.status : live.status;
  const events = demo ? replay.events : live.events;

  // 统一触发粒子爆发
  const fire = useCallback((x: number, y: number, color: string, n = 30) => {
    setBursts([{ x, y, color, count: n, speed: 2.6, life: 70 }]);
  }, []);

  /** 改名即时生效并落盘；空值回落到默认称谓，避免欢迎页出现「欢迎您，」。 */
  const rename = useCallback((v: string) => {
    setObserver(v);
    try {
      localStorage.setItem(NAME_KEY, v);
    } catch {
      /* 隐私模式下写不进去，不影响本次会话 */
    }
  }, []);

  /** 真正显示的名字：留空时用「观测者」，不硬编码任何具体人名。 */
  const displayName = observer.trim() || FALLBACK_NAME;

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

  // 选靶场 / 仓库 → 真接口创建 run → 欢迎页 → 运行页
  const pick = async (p: { name: string; id: string; repo: string; revision?: string }) => {
    setPaper({ name: p.name, id: p.id });
    setPickError(null);

    if (!demo) {
      // 契约 §4.1：repo_url 必填，revision / command / expected_artifact /
      // python_version / timeout_seconds / max_repairs 均可选，202 返回 run_id。
      // 这里只提交 repo_url + revision：命中内置靶场时计划来自后端 manifest
      //（runner.py:546-555）；显式传 command 反而会改走 LLM 生成计划。
      setCreating(true);
      try {
        const res = await createRun({ repo_url: p.repo, revision: p.revision });
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

  /**
   * 回到选择页，准备跑下一篇。
   *
   * 之前没有这条路：从选择页进了运行页就再也出不来，跑完只能刷新浏览器。
   *
   * **只由用户显式点击触发**（顶栏「← 换一篇」、终态横幅「换个仓库再跑」），
   * 不做「终态 N 秒后自动返回」：真沙箱跑一次几十秒，结果页一停就走掉，
   * 报告 / 补丁 / 环境 / 失败原因都来不及看；而 backToSelect 会清掉 runId，
   * 回去之后就再也打不开这次运行的任何产物，只能重跑。
   */
  const backToSelect = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current);
    setRunId(null);
    setPickError(null);
    setCancelling(false);
    setScene("select");
  }, []);

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
            <SelectScene
              onPick={pick}
              disabled={creating}
              error={pickError}
              demo={demo}
              observer={observer}
              onObserver={rename}
            />
          </SceneShell>
        )}

        {scene === "welcome" && (
          <SceneShell key="welcome">
            <WelcomeScene paperName={paper.name || "目标仓库"} userName={displayName} />
          </SceneShell>
        )}

        {scene === "run" && (
          <SceneShell key="run">
            <RunScene
              mode={mode}
              status={status}
              events={events}
              targetName={paper.name || undefined}
              onBack={backToSelect}
              pollError={demo ? null : live.error}
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
