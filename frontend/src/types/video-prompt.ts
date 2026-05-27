export type VideoReferenceKind =
  | "character_sheet"
  | "scene_sheet"
  | "prop_sheet"
  | "start_image"
  | "end_image"
  | "previous_storyboard"
  | "extra";

export interface VideoReferenceImageDTO {
  kind: VideoReferenceKind;
  label: string;
  url: string;
  filename: string;
  relative_path: string;
}

export interface VideoPromptBundleDTO {
  shot_id: string;
  prompt: string;
  duration_seconds: number;
  aspect_ratio: string;
  reference_images: VideoReferenceImageDTO[];
}
