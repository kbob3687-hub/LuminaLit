import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, MotionConfig, useReducedMotion } from "motion/react";
import { ParticleField, type ParticleBurst } from "./components/ParticleField";
import { SelectScene, type PaperOption } from "./components/SelectScene";
import { PreflightScene } from "./components/PreflightScene";
import { WelcomeScene } from "./components/WelcomeScene";
import { RunScene } from "./components/RunScene";
import { ResultScene } from "./components/ResultScene";
import { useRunPolling } from "./hooks/useRunPolling";
import { useDemoRun } from "./hooks/useDemoRun";
import { createRun, createPreflight, cancelRun, ApiError, healthDetails } from "./api";
import { finiteNonNegative, parseRunId, parseScene, resolveRunMode, type Scene } from "./deepLink";
import type { HealthDetails, PreflightResult } from "./types";

/** 截图模式下把所有过渡时长压成 0，避免无头渲染抓到半透明中间帧。 */
const STILL = new URLSearchParams(window.location.search).get("still") === "1";

/**
 * 观测者名字：存在浏览器本地，不再写死一个人名。
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
  const [scene, setScene] = useState<Scene>(() =>
    // 深链走白名单：伪造的 preflight、非法 run 参数一律回退选择页。
    parseScene(new URLSearchParams(window.location.search)),
  );
  const sceneRef = useRef(scene);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  /** 靶场名由用户点的那张卡决定。 */
  const [paper, setPaper] = useState({ name: "", id: "" });
  const [observer, setObserver] = useState(readName);
  const [runId, setRunId] = useState<string | null>(() =>
    // ?run=<id> 直接盯一个已存在的 run；非 32 位十六进制不进 API 路径。
    parseRunId(new URLSearchParams(window.location.search)),
  );
  const [creating, setCreating] = useState(false);
  const [pickError, setPickError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<{ runId: string; message: string } | null>(null);
  const timer = useRef<number | null>(null);
  const pickLock = useRef(false);
  const cancelLock = useRef(false);
  const mounted = useRef(false);

  /** 截图/演示直链：?scene=run / ?scene=result，?run=<id> 作为运行上下文。 */
  const q = new URLSearchParams(window.location.search);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  const showScene = (next: Scene) => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    sceneRef.current = next;
    setScene(next);
  };

  const welcome = () => {
    showScene("welcome");
    timer.current = window.setTimeout(() => {
      if (mounted.current && sceneRef.current === "welcome") showScene("run");
    }, 3050);
  };

  const startRun = (id: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("run", id);
    url.searchParams.delete("scene");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    setRunId(id);
    setPreflight(null);
    welcome();
  };

  /**
   * 数据来源只有两种，且界面上必须能看出来是哪种：
   *
   *   live   —— 后端活着：状态和事件全部来自 GET /api/runs/{id}，前端不改写。
   *   replay —— 离线回放：后端没起来时走这条。
   *
   * 探测优先于配置：启动时打一次 /api/health（1.5s 超时）。
   */
  const apiBase = import.meta.env.VITE_API_BASE_URL as string | undefined;

  /** null = 还在探测 */
  const [healthy, setHealthy] = useState<boolean | null>(null);
  /** 逐项健康检查（§11.3）：后端未实现时保持 null，界面明确说「未提供」。 */
  const [healthInfo, setHealthInfo] = useState<HealthDetails | null>(null);
  const [healthInfoError, setHealthInfoError] = useState<string | null>(null);

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

  useEffect(() => {
    let alive = true;
    // 只做一次只读探测，不发起模型推理；失败时保留 null，不拿 200 冒充就绪。
    void healthDetails()
      .then((d) => { if (alive) setHealthInfo(d); })
      .catch((e: unknown) => {
        if (alive) setHealthInfoError(e instanceof Error ? e.message : String(e));
      });
    return () => { alive = false; };
  }, []);

  // demo=1 优先于 run 深链；否则已有任务直链使用真实 API，探测失败才回退回放。
  const mode: "live" | "replay" = resolveRunMode(q, {
    healthy,
    envDemo: import.meta.env.VITE_DEMO === "1",
  });
  const demo = mode === "replay";

  const live = useRunPolling(mode === "live" ? runId : null);
  const replay = useDemoRun(demo, finiteNonNegative(q.get("t")));
  const status = demo ? replay.status : live.status;
  const events = demo ? replay.events : live.events;

  /** 改名即时生效并落盘；空值回落到默认称谓。 */
  const rename = useCallbackNameSetter(setObserver);

  /** 真正显示的名字：留空时用「观测者」。 */
  const displayName = observer.trim() || FALLBACK_NAME;

  // 内置靶场先冻结确认单；自定义仓库保留探索运行；回放绝不发写请求。
  const pick = async (p: PaperOption) => {
    if (healthy === null || pickLock.current || sceneRef.current !== "select") return;
    pickLock.current = true;
    setCreating(true);
    setPaper({ name: p.name, id: p.id });
    setPickError(null);
    setCancelError(null);
    try {
      if (demo) {
        welcome();
      } else if (p.id !== "custom") {
        const result = await createPreflight({ target_id: p.id, ...p.preflight });
        if (!mounted.current) return;
        setPreflight(result);
        showScene("preflight");
      } else {
        const res = await createRun({ repo_url: p.repo });
        if (!mounted.current) return;
        startRun(res.run_id);
      }
    } catch (err) {
      if (!mounted.current) return;
      const detail = err instanceof Error ? err.message : String(err);
      const code = err instanceof ApiError && err.code ? `（${err.code}）` : "";
      const advice = p.id === "custom" && (!(err instanceof ApiError) || err.status >= 500)
        ? "服务可能已创建任务，请先核对运行记录，避免重复提交。"
        : "可稍后重试。";
      setPickError(`${p.id === "custom" ? "创建探索任务" : "创建静态预检"}失败：${detail}${code} ${advice}`);
    } finally {
      pickLock.current = false;
      if (mounted.current) setCreating(false);
    }
  };

  return (
    <MotionConfig reducedMotion="user">
    <div className="relative h-full overflow-hidden">
      <GridLayer />
      <AmbientParticles />

      <AnimatePresence mode="wait">
        {scene === "select" && (
          <SceneShell key="select">
            <SelectScene
              onPick={pick}
              disabled={creating || healthy === null}
              busy={creating}
              error={pickError}
              demo={demo}
              observer={observer}
              onObserver={rename}
              health={healthInfo}
              healthError={healthInfoError}
            />
          </SceneShell>
        )}

        {scene === "preflight" && preflight && !demo && (
          <SceneShell key="preflight">
            <PreflightScene
              initialPreflight={preflight}
              onBack={() => {
                setPreflight(null);
                setPickError(null);
                showScene("select");
              }}
              onStarted={(id) => {
                if (sceneRef.current !== "preflight") return;
                startRun(id);
              }}
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
              pollError={demo ? null : live.error}
              pollRetrying={!demo && live.retrying}
              cancelError={cancelError?.runId === runId ? cancelError.message : null}
              onShowResult={() => showScene("result")}
              onBack={() => {
                // 清掉运行直链，保留显式 demo/live 等模式标记。
                const params = new URLSearchParams(window.location.search);
                params.delete("run");
                params.delete("scene");
                const search = params.toString();
                window.history.replaceState(null, "", `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`);
                setRunId(null);
                setPaper({ name: "", id: "" });
                setPickError(null);
                setCancelError(null);
                setPreflight(null);
                showScene("select");
              }}
              cancelling={cancelling}
              onCancel={async () => {
                if (!runId || demo || cancelLock.current || sceneRef.current !== "run") return;
                cancelLock.current = true;
                setCancelling(true);
                setCancelError(null);
                try {
                  await cancelRun(runId);
                } catch (error) {
                  setCancelError({
                    runId,
                    message: `取消请求未成功：${error instanceof Error ? error.message : String(error)}。任务可能仍在运行，请检查进度后重试取消。`,
                  });
                } finally {
                  cancelLock.current = false;
                  if (mounted.current) setCancelling(false);
                }
              }}
            />
          </SceneShell>
        )}

        {scene === "result" && (
          <SceneShell key="result">
            <ResultScene
              status={status}
              runId={runId}
              isDemo={demo}
              onBack={() => showScene("run")}
              onNewRun={() => {
                const params = new URLSearchParams(window.location.search);
                params.delete("run");
                params.delete("scene");
                params.delete("panel");
                const search = params.toString();
                window.history.replaceState(null, "", `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`);
                setRunId(null);
                setPaper({ name: "", id: "" });
                setPreflight(null);
                showScene("select");
              }}
              onTechnical={() => {
                const url = new URL(window.location.href);
                url.searchParams.set("panel", "technical");
                window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
                showScene("run");
              }}
            />
          </SceneShell>
        )}
      </AnimatePresence>
    </div>
    </MotionConfig>
  );
}

const NO_BURSTS: ParticleBurst[] = [];
function AmbientParticles() {
  const reduced = useReducedMotion();
  return reduced || STILL ? null : <ParticleField bursts={NO_BURSTS} />;
}

/** 场景外壳：统一进出场过渡（仅 opacity，不做位移/模糊）。 */
function SceneShell({ children }: { children: React.ReactNode }) {
  return (
    <motion.section
      className="absolute inset-0 z-10"
      initial={STILL ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: STILL ? 0 : 0.18 }}
    >
      {children}
    </motion.section>
  );
}

/** 极弱网格底纹：静态单色，无视差、无动画。 */
function GridLayer() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 z-0"
      style={{
        backgroundImage:
          "linear-gradient(rgb(255 255 255 / 0.02) 1px, transparent 1px), linear-gradient(90deg, rgb(255 255 255 / 0.02) 1px, transparent 1px)",
        backgroundSize: "56px 56px",
      }}
    />
  );
}

function useCallbackNameSetter(set: (v: string) => void) {
  return (v: string) => {
    set(v);
    try {
      localStorage.setItem(NAME_KEY, v);
    } catch {
      /* 隐私模式下写不进去，不影响本次会话 */
    }
  };
}
