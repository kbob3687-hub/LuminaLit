import { useEffect, useRef, useState } from "react";
import { bundleDownloadUrl, fetchBundle } from "../api";
import type { BundleResult } from "../types";

/** 两个结果视图共用：错误留在页面，只有成功响应才保存为 ZIP。 */
export function BundleDownload({ runId, bundle }: { runId: string | null; bundle: BundleResult | null | undefined }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    setBusy(false);
    setError(null);
    return () => { request.current?.abort(); };
  }, [runId, bundle?.sha256]);

  const download = async () => {
    if (request.current && !request.current.signal.aborted) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(null);
    try {
      const blob = await fetchBundle(runId, bundle, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "reproduce.zip";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "下载失败，请稍后重试。");
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        request.current = null;
      }
    }
  };

  if (!bundleDownloadUrl(runId, bundle)) return null;
  return (
    <div className="mt-3">
      <button type="button" disabled={busy} onClick={() => void download()}
        className="cursor-pointer rounded text-sm text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent disabled:opacity-50">
        {busy ? "正在下载材料…" : "下载复跑材料"}
      </button>
      {error && <p role="alert" className="mt-2 text-sm leading-6 text-reject">{error}</p>}
    </div>
  );
}
