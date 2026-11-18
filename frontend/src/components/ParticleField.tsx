import { useEffect, useRef } from "react";

/**
 * 全屏鼠标跟随粒子层。
 *
 * 为什么用 canvas 而不是 DOM：DOM 做粒子必然掉帧，300 个 div 每帧改 transform
 * 在 165Hz 屏上直接卡死。
 *
 * 粒子有语义（DESIGN.md §1.7.2）：背景漂移是环境，爆发的粒子绑定具体状态变化。
 */
export interface ParticleBurst {
  x: number;
  y: number;
  color: string;
  count?: number;
  speed?: number;
  life?: number;
  gravity?: number;
  size?: number;
}

const MAX_PARTICLES = 420;

const COLOR = {
  accent: "108,143,255",
  reject: "255,92,71",
  pass: "46,230,168",
  white: "255,255,255",
} as const;

export const PARTICLE_COLORS = COLOR;

type P = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  life: number;
  max: number;
  color: string;
  alpha: number;
  gravity: number;
  ambient: boolean;
  depth: number;
};

export function ParticleField({ bursts }: { bursts: ParticleBurst[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particles = useRef<P[]>([]);
  const mouse = useRef({ x: 0, y: 0, tx: 0, ty: 0 });
  const size = useRef({ w: 0, h: 0 });

  // ── 初始化画布 + 背景粒子 ────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = window.innerWidth;
      const h = window.innerHeight;
      size.current = { w, h };
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();

    // 背景环境粒子
    if (particles.current.length === 0) {
      const { w, h } = size.current;
      for (let i = 0; i < 110; i++) {
        particles.current.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.1,
          vy: (Math.random() - 0.5) * 0.1,
          r: Math.random() * 1.5 + 0.5,
          life: Infinity,
          max: Infinity,
          color: COLOR.white,
          alpha: 0.09 + Math.random() * 0.13,
          gravity: 0,
          ambient: true,
          depth: Math.random(),
        });
      }
    }

    const onMove = (e: MouseEvent) => {
      mouse.current.tx = (e.clientX / window.innerWidth) * 2 - 1;
      mouse.current.ty = (e.clientY / window.innerHeight) * 2 - 1;
      document.documentElement.style.setProperty("--cx", `${e.clientX}px`);
      document.documentElement.style.setProperty("--cy", `${e.clientY}px`);
    };
    window.addEventListener("mousemove", onMove, { passive: true });
    window.addEventListener("resize", resize);

    const loop = () => {
      const { w, h } = size.current;
      ctx.clearRect(0, 0, w, h);

      // 平滑插值
      mouse.current.x += (mouse.current.tx - mouse.current.x) * 0.09;
      mouse.current.y += (mouse.current.ty - mouse.current.y) * 0.09;
      document.documentElement.style.setProperty("--mx", mouse.current.x.toFixed(4));
      document.documentElement.style.setProperty("--my", mouse.current.y.toFixed(4));

      const list = particles.current;
      for (let i = list.length - 1; i >= 0; i--) {
        const p = list[i];
        p.x += p.vx;
        p.y += p.vy;

        let alpha: number;
        if (p.ambient) {
          alpha = p.alpha;
          if (p.x < -30) p.x = w + 30;
          if (p.x > w + 30) p.x = -30;
          if (p.y < -30) p.y = h + 30;
          if (p.y > h + 30) p.y = -30;
        } else {
          p.vy += p.gravity;
          p.vx *= 0.985;
          p.vy *= 0.985;
          p.life -= 1;
          if (p.life <= 0) {
            list.splice(i, 1);
            continue;
          }
          alpha = p.alpha * (p.life / p.max);
        }

        // 视差：背景粒子随鼠标轻移
        const par = p.ambient ? p.depth * 12 : 0;
        const px = p.x + mouse.current.x * par;
        const py = p.y + mouse.current.y * par;

        ctx.beginPath();
        ctx.arc(px, py, p.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${p.color},${alpha})`;
        ctx.fill();

        // 爆发粒子的柔晕（同色低透明度，不是 glow 贴图）
        if (!p.ambient && p.r > 1.3) {
          ctx.beginPath();
          ctx.arc(px, py, p.r * 2.9, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${p.color},${alpha * 0.14})`;
          ctx.fill();
        }
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("resize", resize);
    };
  }, []);

  // ── 外部触发的爆发 ──────────────────────────────────
  useEffect(() => {
    if (bursts.length === 0) return;
    const list = particles.current;
    for (const b of bursts) {
      const count = b.count ?? 34;
      const speed = b.speed ?? 2.4;
      const life = b.life ?? 68;
      const gravity = b.gravity ?? 0.024;
      const scale = b.size ?? 1;
      for (let i = 0; i < count; i++) {
        if (list.filter((p) => !p.ambient).length >= MAX_PARTICLES) break;
        const ang = Math.random() * Math.PI * 2;
        const v = speed * (0.35 + Math.random());
        list.push({
          x: b.x,
          y: b.y,
          vx: Math.cos(ang) * v,
          vy: Math.sin(ang) * v,
          r: (Math.random() * 1.8 + 0.9) * scale,
          life,
          max: life,
          color: b.color,
          alpha: 0.95,
          gravity,
          ambient: false,
          depth: 0,
        });
      }
    }
  }, [bursts]);

  return <canvas ref={canvasRef} className="pointer-events-none fixed inset-0 z-[1]" />;
}
