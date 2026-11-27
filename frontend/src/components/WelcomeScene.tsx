import { motion } from "motion/react";

/**
 * 场景 2：欢迎页。
 *
 * 序列（总长约 3s，之后进入运行场景）：品牌字逐字升起 → 欢迎语淡入 →
 * 论文名淡入 → 进度细线填满。无光环、无扫描线、无发光文字。
 */
const BRAND = "PAPERHARNESS";

export function WelcomeScene({ paperName, userName }: { paperName: string; userName: string }) {
  return (
    <div className="relative flex h-full items-center justify-center">
      <div className="px-6 text-center">
        {/* 品牌字：逐字升起 */}
        <motion.div
          className="flex justify-center text-[clamp(40px,7vw,88px)] leading-none font-semibold tracking-[-.04em] text-ink"
          initial="hidden"
          animate="show"
          variants={{
            hidden: {},
            show: { transition: { staggerChildren: 0.03, delayChildren: 0.1 } },
          }}
        >
          {BRAND.split("").map((ch, i) => (
            <motion.span
              key={i}
              className="inline-block"
              variants={{
                hidden: { opacity: 0, y: "24%" },
                show: { opacity: 1, y: "0%" },
              }}
              transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            >
              {ch}
            </motion.span>
          ))}
        </motion.div>

        {/* 欢迎语 */}
        <motion.p
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.55, ease: [0.16, 1, 0.3, 1] }}
          className="mt-7 text-[15px] text-ink-mid"
        >
          欢迎您，<b className="font-medium text-ink">{userName}</b>
        </motion.p>

        {/* 论文名 */}
        <motion.p
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.8, ease: [0.16, 1, 0.3, 1] }}
          className="mt-3 font-mono text-[11px] text-ink-muted"
        >
          正在准备 · {paperName}
        </motion.p>

        {/* 进度细线 */}
        <div className="mx-auto mt-12 h-px w-[184px] overflow-hidden bg-rule">
          <motion.div
            className="h-full bg-accent"
            initial={{ width: "0%" }}
            animate={{ width: "100%" }}
            transition={{ duration: 1.4, delay: 0.9, ease: [0.16, 1, 0.3, 1] }}
          />
        </div>
      </div>
    </div>
  );
}
