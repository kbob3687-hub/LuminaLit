import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { SelectScene, type PaperOption } from "./components/SelectScene";
import { PreflightScene } from "./components/PreflightScene";
import { WelcomeScene } from "./components/WelcomeScene";
import { RunScene } from "./components/RunScene";
import { useRunPolling } from "./hooks/useRunPolling";
import { useDemoRun } from "./hooks/useDemoRun";
import { createRun, createPreflight, cancelRun, ApiError } from "./api";
import { finiteNonNegative, parseRunId, parseScene, type Scene } from "./deepLink";
import type { PreflightResult } from "./types";

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

  /** 截图/演示直链：?scene=run / ?scene=welcome，?run=<id> 直接盯一个 run。 */
  const q = new URLSearchParams(window.location.search);
  const linkedRun = parseRunId(q);

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
  const wantReplay = q.get("demo") === "1" || import.meta.env.VITE_DEMO === "1";
  const wantLive = q.get("live") === "1" || Boolean(linkedRun);

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

  // 已有任务直链始终使用真实 API；探测失败才自动回退离线回放。
  const mode: "live" | "replay" = linkedRun
    ? "live"
    : wantReplay
      ? "replay"
      : wantLive || healthy !== false
        ? "live"
        : "replay";
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
        const result = await createPreflight({ target_id: p.id });
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
    <div className="relative h-full overflow-hidden">
      <GridLayer />

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
      </AnimatePresence>
    </div>
  );
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
