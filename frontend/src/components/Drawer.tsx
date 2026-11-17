import { AnimatePresence, motion } from "motion/react";
import { useEffect } from "react";
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
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50">
          <motion.div
            className="absolute inset-0 bg-void/60 backdrop-blur-[2px]"
            initial={STILL ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
          />
          <motion.aside
            className="absolute inset-y-0 right-0 flex w-[min(560px,94vw)] flex-col border-l border-rule-hi bg-surface/95 shadow-[-30px_0_80px_-30px_rgb(0_0_0_/_0.9)] backdrop-blur-xl"
            initial={STILL ? false : { x: 40, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 40, opacity: 0 }}
            transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
            role="dialog"
            aria-modal="true"
          >
            <header className="flex items-start justify-between gap-4 border-b border-rule px-5 py-4">
              <div className="min-w-0">
                <div className="truncate text-[13px] font-semibold tracking-[.02em] text-ink">
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
                esc
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          </motion.aside>
        </div>
      )}
    </AnimatePresence>
  );
}
