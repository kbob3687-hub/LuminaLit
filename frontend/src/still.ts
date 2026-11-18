/** 截图模式（?still=1）：关掉入场动画，避免无头渲染抓到半透明/位移的中间帧。 */
export const STILL = new URLSearchParams(window.location.search).get("still") === "1";
