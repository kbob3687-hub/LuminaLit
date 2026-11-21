import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useRef } from "react";
import { STILL } from "../still";

/**
 * 右侧抽屉：ACI 校验过程 / 自愈回环全量 / 阶段详情 / 执行计划都走这一个容器。
 * ESC 或点遮罩关闭——不再要求"必须先找左上角返回"。
 */
export function Drawer({
  open,
  title,
  subtitle,
  onClose,
  children,
  wide,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: React.ReactNode;
  /** 报告 / 计划这类含长文本或 diff 的面板需要更宽，否则只能横向滚。 */
  wide?: boolean;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  const reduceMotion = useReducedMotion();
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close.current();
      if (event.key !== "Tab") return;
      const items = Array.from(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), a[href], summary, [tabindex='0']") ?? []).filter((item) => item.getClientRects().length > 0);
      const first = items[0];
      const last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50">
          <motion.div
            className="absolute inset-0 bg-void/60 backdrop-blur-[2px]"
            initial={STILL || reduceMotion ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.2 }}
            onClick={onClose}
          />
          <motion.aside
            className={
              wide
                ? "absolute inset-y-0 right-0 flex w-[min(760px,96vw)] flex-col border-l border-rule-hi bg-surface/95 shadow-[-30px_0_80px_-30px_rgb(0_0_0_/_0.9)] backdrop-blur-xl"
                : "absolute inset-y-0 right-0 flex w-[min(560px,94vw)] flex-col border-l border-rule-hi bg-surface/95 shadow-[-30px_0_80px_-30px_rgb(0_0_0_/_0.9)] backdrop-blur-xl"
            }
            initial={STILL || reduceMotion ? false : { x: 40, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 40, opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.32, ease: [0.16, 1, 0.3, 1] }}
            ref={dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
          >
            <header className="flex items-start justify-between gap-4 border-b border-rule px-5 py-4">
              <div className="min-w-0">
                <div id={titleId} className="truncate text-base font-semibold text-ink">
                  {title}
                </div>
                {subtitle && (
                  <div className="mt-1 truncate font-mono text-[10.5px] tracking-[.03em] text-ink-muted">
                    {subtitle}
                  </div>
                )}
              </div>
              <button
                onClick={onClose}
                className="shrink-0 cursor-pointer rounded-[6px] border border-rule-hi px-2.5 py-1 font-mono text-[11px] text-ink-muted transition-colors hover:border-ink-muted hover:text-ink"
              >
                关闭
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          </motion.aside>
        </div>
      )}
    </AnimatePresence>
  );
}
