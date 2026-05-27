import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Upload, X } from "lucide-react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";

interface Props {
  projectName: string;
  episode: number;
  segmentId: string;
  open: boolean;
  onClose: () => void;
}

const ACCEPT = "video/mp4,video/quicktime,video/webm";
const MAX_SIZE = 200 * 1024 * 1024;

export function VideoImportModal({ projectName, episode, segmentId, open, onClose }: Props) {
  const { t } = useTranslation("dashboard");
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const handleFile = (f: File) => {
    setError(null);
    if (f.size > MAX_SIZE) {
      setError(
        t("video_import_modal_file_too_large", {
          size_mb: Math.round(f.size / 1024 / 1024),
        }),
      );
      return;
    }
    setFile(f);
  };

  const submit = async () => {
    if (!file) return;
    setUploading(true);
    setProgress(0);
    try {
      const result = await API.importExternalVideo(
        projectName,
        episode,
        segmentId,
        file,
        (l, total) => {
          setProgress(Math.round((l / total) * 100));
        },
      );
      // bust 缓存：fingerprint 用时间戳，让 MediaCard 重新拉视频/缩略图
      useProjectsStore.getState().updateAssetFingerprints({
        [result.video_path]: Date.now(),
        [result.thumbnail_path]: Date.now(),
      });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("video_import_modal_title")}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <button
        type="button"
        aria-label={t("shot_detail_cancel")}
        className="absolute inset-0 h-full w-full"
        onClick={onClose}
      />
      <div
        className="relative w-full max-w-md rounded-lg p-5"
        style={{ background: "var(--color-surface-1)", color: "var(--color-text-1)" }}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">{t("video_import_modal_title")}</h2>
          <button type="button" onClick={onClose} aria-label={t("shot_detail_cancel")} className="focus-ring rounded p-1">
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="mb-3 text-xs opacity-70">{t("video_import_modal_replace_warning")}</p>

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
          aria-label={t("video_import_modal_drag_hint")}
          className="focus-ring w-full rounded border border-dashed py-8 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          style={{ borderColor: "var(--color-hairline)" }}
        >
          {file ? file.name : t("video_import_modal_drag_hint")}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleFile(f);
          }}
        />

        {uploading && (
          <div className="mt-3">
            <div className="h-2 w-full overflow-hidden rounded" style={{ background: "var(--color-surface-2)" }}>
              <div
                className="h-full transition-all"
                style={{ width: `${progress}%`, background: "var(--color-accent)" }}
              />
            </div>
            <p className="mt-1 text-xs opacity-60">
              {t("video_import_modal_uploading")} {progress}%
            </p>
          </div>
        )}

        {error && (
          <p className="mt-3 text-xs" style={{ color: "var(--color-error)" }}>
            {error}
          </p>
        )}

        <button
          type="button"
          onClick={() => {
            void submit();
          }}
          disabled={!file || uploading}
          className="focus-ring mt-4 inline-flex w-full items-center justify-center gap-2 rounded py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          style={{ background: "var(--color-accent)", color: "oklch(0.14 0 0)" }}
        >
          <Upload className="h-4 w-4" />
          {t("video_import_modal_title")}
        </button>
      </div>
    </div>
  );
}
