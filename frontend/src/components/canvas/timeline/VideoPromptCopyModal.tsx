import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Download, X, Check, Package } from "lucide-react";
import { API } from "@/api";
import type { VideoPromptBundleDTO } from "@/types/video-prompt";

interface Props {
  projectName: string;
  episode: number;
  segmentId: string;
  open: boolean;
  onClose: () => void;
}

export function VideoPromptCopyModal({
  projectName,
  episode,
  segmentId,
  open,
  onClose,
}: Props) {
  const { t } = useTranslation("dashboard");
  const [bundle, setBundle] = useState<VideoPromptBundleDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [promptCopied, setPromptCopied] = useState(false);
  const [imgCopiedIdx, setImgCopiedIdx] = useState<number | null>(null);

  useEffect(() => {
    if (!open) return;
    const load = async () => {
      setLoading(true);
      setBundle(null);
      try {
        const data = await API.getVideoPromptBundle(projectName, episode, segmentId);
        setBundle(data);
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [open, projectName, episode, segmentId]);

  if (!open) return null;

  const copyPrompt = async () => {
    if (!bundle) return;
    await navigator.clipboard.writeText(bundle.prompt);
    setPromptCopied(true);
    setTimeout(() => setPromptCopied(false), 1500);
  };

  const copyImage = async (url: string, idx: number) => {
    try {
      const resp = await fetch(url);
      const blob = await resp.blob();
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
    } catch {
      // 回退：复制 URL
      await navigator.clipboard.writeText(window.location.origin + url);
    }
    setImgCopiedIdx(idx);
    setTimeout(() => setImgCopiedIdx(null), 1500);
  };

  const handleOverlayKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") onClose();
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("video_prompt_modal_title")}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      {/* Backdrop — keyboard-accessible overlay that closes on click or Escape */}
      <div
        role="button"
        tabIndex={0}
        aria-label="close dialog"
        className="absolute inset-0"
        onClick={onClose}
        onKeyDown={handleOverlayKeyDown}
      />

      {/* Modal panel */}
      <div
        className="relative w-full max-w-2xl rounded-lg p-5 max-h-[90vh] overflow-y-auto"
        style={{ background: "var(--color-surface-1)", color: "var(--color-text-1)" }}
      >
        {/* Header */}
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">
            {t("video_prompt_modal_title")}
            {bundle && (
              <span className="ml-2 text-xs opacity-60">
                Shot {bundle.shot_id} · {bundle.duration_seconds}s · {bundle.aspect_ratio}
              </span>
            )}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="close"
            className="focus-ring rounded p-1"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Loading */}
        {loading && <p className="text-sm opacity-60">{t("loading")}</p>}

        {/* Content */}
        {bundle && (
          <>
            {/* Prompt section */}
            <div className="mb-5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-semibold opacity-70">
                  {t("video_prompt_modal_prompt_label")}
                </span>
                <button
                  type="button"
                  onClick={() => { void copyPrompt(); }}
                  className="focus-ring inline-flex items-center gap-1 rounded px-2 py-1 text-xs"
                  style={{ background: "var(--color-accent)", color: "oklch(0.14 0 0)" }}
                >
                  {promptCopied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {promptCopied
                    ? t("video_prompt_modal_copied")
                    : t("video_prompt_modal_copy_all")}
                </button>
              </div>
              <pre
                className="max-h-60 overflow-auto rounded p-3 text-xs whitespace-pre-wrap break-words"
                style={{ background: "var(--color-surface-2)" }}
              >
                {bundle.prompt}
              </pre>
            </div>

            {/* Reference images section */}
            <div className="mb-5">
              <span className="text-xs font-semibold opacity-70">
                {t("video_prompt_modal_refs_label", {
                  count: bundle.reference_images.length,
                })}
              </span>
              {bundle.reference_images.length === 0 ? (
                <p className="mt-2 text-sm opacity-60">{t("video_prompt_modal_no_refs")}</p>
              ) : (
                <div className="mt-2 grid grid-cols-3 gap-3">
                  {bundle.reference_images.map((ref, idx) => (
                    <div
                      key={ref.relative_path}
                      className="overflow-hidden rounded"
                      style={{ border: "1px solid var(--color-hairline)" }}
                    >
                      <img
                        src={ref.url}
                        alt={ref.label}
                        className="aspect-square w-full object-cover"
                      />
                      <div className="p-2">
                        <div className="truncate text-xs font-medium">{ref.label}</div>
                        <div className="truncate text-[10px] opacity-50">{ref.filename}</div>
                        <div className="mt-1 flex gap-1">
                          <button
                            type="button"
                            onClick={() => { void copyImage(ref.url, idx); }}
                            className="focus-ring flex-1 inline-flex items-center justify-center gap-1 rounded p-1 text-[10px]"
                            style={{ background: "var(--color-surface-2)" }}
                            aria-label={t("video_prompt_modal_copy_image")}
                          >
                            {imgCopiedIdx === idx ? (
                              <Check className="h-3 w-3" />
                            ) : (
                              <Copy className="h-3 w-3" />
                            )}
                          </button>
                          <a
                            href={ref.url}
                            download={ref.filename}
                            className="focus-ring flex-1 inline-flex items-center justify-center gap-1 rounded p-1 text-[10px]"
                            style={{ background: "var(--color-surface-2)" }}
                            aria-label={t("video_prompt_modal_download_image")}
                          >
                            <Download className="h-3 w-3" />
                          </a>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* ZIP download */}
            <a
              href={API.getVideoPromptBundleZipUrl(projectName, episode, segmentId)}
              download
              className="focus-ring inline-flex w-full items-center justify-center gap-2 rounded py-2 text-sm"
              style={{
                background: "var(--color-surface-2)",
                color: "var(--color-text-1)",
              }}
            >
              <Package className="h-4 w-4" />
              {t("video_prompt_modal_download_zip")}
            </a>
          </>
        )}
      </div>
    </div>
  );
}
