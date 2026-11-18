import { motion } from "motion/react";

/**
 * 场景 2：欢迎动画。
 *
 * 序列（总长约 3s，之后进入运行场景）：
 *   0.0s  三道光环从中心扩散
 *   0.2s  扫描线横切
 *   0.1s  品牌字逐字升起（stagger 0.035s）
 *   0.55s 欢迎语淡入
 *   0.85s  论文名淡入
 *   1.0s  进度细线填满
 *
 * 用 motion 的 variants + staggerChildren，不手写延迟，避免 CSS 类名丢失导致整块不显示。
 */
const BRAND = "PAPERHARNESS";

export function WelcomeScene({ paperName, userName }: { paperName: string; userName: string }) {
  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden">
      {/* 光环 */}
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          className="pointer-events-none absolute rounded-full border"
          style={{
            width: 420 + i * 220,
            height: 420 + i * 220,
            borderColor: `rgb(108 143 255 / ${0.5 - i * 0.14})`,
          }}
          initial={{ opacity: 0, scale: 0.35 }}
          animate={{ opacity: [0, 1, 0], scale: [0.35, 1.05, 1.5] }}
          transition={{ duration: 1.9, delay: i * 0.16, ease: [0.16, 1, 0.3, 1] }}
        />
      ))}

      {/* 扫描线 */}
      <motion.div
        className="pointer-events-none absolute h-px shadow-[0_0_18px_rgb(108_143_255)]"
        style={{
          background:
            "linear-gradient(90deg, transparent, #6c8fff, transparent)",
        }}
        initial={{ width: 0, opacity: 0 }}
        animate={{ width: ["0px", "min(760px,80vw)", "min(1100px,95vw)"], opacity: [0, 1, 0] }}
        transition={{ duration: 1.5, ease: [0.16, 1, 0.3, 1] }}
      />

      <div className="relative z-[2] px-6 text-center">
        {/* 品牌字：逐字升起 */}
        <motion.div
          className="flex justify-center text-[clamp(48px,9.5vw,132px)] leading-none font-bold tracking-[-.045em]"
          initial="hidden"
          animate="show"
          variants={{
            hidden: {},
            show: { transition: { staggerChildren: 0.035, delayChildren: 0.1 } },
          }}
        >
          {BRAND.split("").map((ch, i) => (
            <motion.span
              key={i}
              className="inline-block text-white"
              style={{
                textShadow:
                  "0 0 48px rgb(108 143 255 / .65), 0 0 90px rgb(108 143 255 / .32)",
              }}
              variants={{
                hidden: { opacity: 0, y: "30%" },
                show: { opacity: 1, y: "0%" },
              }}
              transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
            >
              {ch}
            </motion.span>
          ))}
        </motion.div>

        {/* 欢迎语 */}
        <motion.p
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.85, delay: 0.55, ease: [0.16, 1, 0.3, 1] }}
          className="mt-8 text-[clamp(17px,2.1vw,23px)] tracking-[.22em] text-[#c8d2e6] [text-shadow:0_0_30px_rgb(108_143_255_/_0.4)]"
        >
          欢迎您，
          <b className="font-semibold tracking-[.06em] text-white">{userName}</b>
        </motion.p>

        {/* 论文名 */}
        <motion.p
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.85, ease: [0.16, 1, 0.3, 1] }}
          className="mt-4 font-mono text-xs tracking-[.16em] text-[#8fadff] [text-shadow:0_0_24px_rgb(108_143_255_/_0.6)]"
        >
          正在准备 · {paperName}
        </motion.p>

        {/* 进度细线 */}
        <div className="mx-auto mt-16 h-px w-[184px] overflow-hidden bg-white/10">
          <motion.div
            className="h-full bg-gradient-to-r from-transparent to-accent"
            initial={{ width: "0%" }}
            animate={{ width: "100%" }}
            transition={{ duration: 1.5, delay: 1.0, ease: [0.16, 1, 0.3, 1] }}
          />
        </div>
      </div>
    </div>
  );
}
