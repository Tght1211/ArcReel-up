import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, X } from "lucide-react";

import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { API, type ImageGenerationTestResponse } from "@/api";

const MAX_PROMPT_CHARS = 1000;

export interface ImageTestModelOption {
  model_id: string;
  display_name: string;
  endpoint: string;
}

interface ImageGenerationTestModalProps {
  providerId: number;
  models: ImageTestModelOption[]; // 已被父组件过滤为 T2I 端点
  onClose: () => void;
}

export function ImageGenerationTestModal({
  providerId,
  models,
  onClose,
}: ImageGenerationTestModalProps) {
  const { t } = useTranslation("dashboard");

  // 默认优先选 model_id 含 "gpt-image" 的，否则取第一个
  const initialModel =
    models.find((m) => m.model_id.toLowerCase().includes("gpt-image")) ?? models[0];
  const [modelId, setModelId] = useState<string>(initialModel?.model_id ?? "");
  const [prompt, setPrompt] = useState<string>(t("image_test_default_prompt"));
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ImageGenerationTestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Escape 键关闭（loading 时禁用）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !loading) onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [loading, onClose]);

  const handleStart = () => {
    setLoading(true);
    setError(null);
    setResult(null);
    API.testCustomProviderImageGeneration(providerId, {
      model_id: modelId,
      prompt: prompt.slice(0, MAX_PROMPT_CHARS),
    })
      .then((resp) => {
        setResult(resp);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        setLoading(false);
      });
  };

  const handleRetry = () => {
    setResult(null);
    setError(null);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="image-test-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
    >
      {/* 透明遮罩按钮：点空白处关闭，loading 时禁用；键盘走 Escape（已有 useEffect） */}
      <button
        type="button"
        aria-label={t("image_test_close")}
        className="absolute inset-0 cursor-default"
        onClick={onClose}
        disabled={loading}
        tabIndex={-1}
      />
      <div
        className="relative w-full max-w-xl rounded-[12px] border border-hairline p-6 shadow-2xl"
        style={{
          background:
            "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.92), oklch(0.16 0.010 265 / 0.92))",
        }}
      >
        <button
          type="button"
          onClick={onClose}
          disabled={loading}
          className="absolute right-3 top-3 rounded-[6px] p-1 text-text-3 transition-colors enabled:hover:text-text disabled:opacity-40"
          aria-label={t("image_test_close")}
        >
          <X className="h-4 w-4" />
        </button>

        <h2
          id="image-test-modal-title"
          className="mb-4 pr-6 text-[15px] font-semibold text-text"
        >
          {t("image_test_modal_title")}
        </h2>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-12 text-[13px] text-text-2">
            <Loader2 className="h-4 w-4 motion-safe:animate-spin" />
            <span>{t("image_test_loading")}</span>
          </div>
        ) : result ? (
          <ImageTestResult result={result} onClose={onClose} onRetry={handleRetry} />
        ) : error ? (
          <ImageTestError error={error} onClose={onClose} onRetry={handleRetry} />
        ) : (
          <ImageTestForm
            models={models}
            modelId={modelId}
            setModelId={setModelId}
            prompt={prompt}
            setPrompt={setPrompt}
            onStart={handleStart}
            onCancel={onClose}
          />
        )}
      </div>
    </div>
  );
}

interface FormProps {
  models: ImageTestModelOption[];
  modelId: string;
  setModelId: (v: string) => void;
  prompt: string;
  setPrompt: (v: string) => void;
  onStart: () => void;
  onCancel: () => void;
}

function ImageTestForm({
  models,
  modelId,
  setModelId,
  prompt,
  setPrompt,
  onStart,
  onCancel,
}: FormProps) {
  const { t } = useTranslation("dashboard");
  const overLimit = prompt.length > MAX_PROMPT_CHARS;

  return (
    <div className="space-y-4">
      <label className="block">
        <span className="mb-1 block text-[11px] uppercase tracking-[0.12em] text-text-3">
          {t("image_test_model_label")}
        </span>
        <select
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          className={INPUT_CLS}
        >
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.display_name} ({m.model_id})
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        <span className="mb-1 block text-[11px] uppercase tracking-[0.12em] text-text-3">
          {t("image_test_prompt_label")}
        </span>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value.slice(0, MAX_PROMPT_CHARS))}
          rows={4}
          className={INPUT_CLS}
        />
        <div
          className={`mt-1 text-right text-[10.5px] ${overLimit ? "text-bad" : "text-text-4"}`}
        >
          {prompt.length} / {MAX_PROMPT_CHARS}
        </div>
      </label>

      <div className="rounded-[6px] border border-warm-ring/40 bg-warm-tint/20 px-3 py-2 text-[11.5px] text-warm-bright">
        {t("image_test_billing_warning")}
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button type="button" onClick={onCancel} className={GHOST_BTN_CLS}>
          {t("common:cancel")}
        </button>
        <button
          type="button"
          onClick={onStart}
          disabled={!modelId}
          className={ACCENT_BTN_CLS}
          style={ACCENT_BUTTON_STYLE}
        >
          {t("image_test_start")}
        </button>
      </div>
    </div>
  );
}

interface ResultProps {
  result: ImageGenerationTestResponse;
  onClose: () => void;
  onRetry: () => void;
}

function ImageTestResult({ result, onClose, onRetry }: ResultProps) {
  const { t } = useTranslation("dashboard");

  if (!result.success) {
    return (
      <ImageTestError
        error={`${result.message} (HTTP ${result.status_code ?? "?"} · ${result.latency_ms}ms)`}
        onClose={onClose}
        onRetry={onRetry}
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="text-[13px] font-medium" style={{ color: "var(--color-good)" }}>
        ✓ {result.message} · {(result.latency_ms / 1000).toFixed(1)}s
      </div>
      {result.image_data_url && (
        <img
          src={result.image_data_url}
          alt="generated"
          className="mx-auto max-h-[400px] rounded-[8px] border border-hairline"
        />
      )}
      {(result.upstream_metadata.model ||
        result.upstream_metadata.size ||
        result.upstream_metadata.quality ||
        result.upstream_metadata.output_format) && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 rounded-[6px] border border-hairline-soft bg-bg-grad-a/40 px-3 py-2 text-[11px] text-text-3">
          {result.upstream_metadata.model && (
            <div>
              <span className="text-text-4">model:</span> {result.upstream_metadata.model}
            </div>
          )}
          {result.upstream_metadata.size && (
            <div>
              <span className="text-text-4">size:</span> {result.upstream_metadata.size}
            </div>
          )}
          {result.upstream_metadata.quality && (
            <div>
              <span className="text-text-4">quality:</span> {result.upstream_metadata.quality}
            </div>
          )}
          {result.upstream_metadata.output_format && (
            <div>
              <span className="text-text-4">format:</span>{" "}
              {result.upstream_metadata.output_format}
            </div>
          )}
        </div>
      )}
      {result.revised_prompt && (
        <details className="text-[12px] text-text-3">
          <summary className="cursor-pointer">{t("image_test_revised_prompt_label")}</summary>
          <p className="mt-1 whitespace-pre-wrap rounded-[6px] border border-hairline-soft bg-bg-grad-a/40 px-3 py-2">
            {result.revised_prompt}
          </p>
        </details>
      )}
      <div className="flex justify-end gap-2 pt-1">
        <button type="button" onClick={onRetry} className={GHOST_BTN_CLS}>
          {t("image_test_retry")}
        </button>
        <button
          type="button"
          onClick={onClose}
          className={ACCENT_BTN_CLS}
          style={ACCENT_BUTTON_STYLE}
        >
          {t("image_test_close")}
        </button>
      </div>
    </div>
  );
}

interface ErrorProps {
  error: string;
  onClose: () => void;
  onRetry: () => void;
}

function ImageTestError({ error, onClose, onRetry }: ErrorProps) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="space-y-3">
      <div className="rounded-[8px] border border-warm-ring/60 bg-warm-tint/25 px-3 py-2 text-[12.5px] text-warm-bright">
        ✗ {error}
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <button type="button" onClick={onRetry} className={GHOST_BTN_CLS}>
          {t("image_test_retry")}
        </button>
        <button
          type="button"
          onClick={onClose}
          className={ACCENT_BTN_CLS}
          style={ACCENT_BUTTON_STYLE}
        >
          {t("image_test_close")}
        </button>
      </div>
    </div>
  );
}
