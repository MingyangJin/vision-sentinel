import type { ChatActionItem, TokenUsage } from "./storage";

export type CameraStatus = "ok" | "degraded" | "offline";
export type Severity = "info" | "warning" | "urgent";
export type ReportStatus = "clear" | "notable" | "urgent" | "error";

export interface SystemHealth {
  ok: boolean;
  bind_host: string;
  non_loopback_warning: boolean;
  cameras_total: number;
  cameras_ok: number;
  topics_total: number;
  topics_active: number;
}

export interface CameraInfo {
  camera_id: string;
  adapter: string;
  speaker_enabled: boolean;
  volume_cap: number;
  has_live_stream: boolean;
  segment_count: number;
  health: {
    camera_id: string;
    status: CameraStatus;
    checked_at: string;
    last_segment_at: string | null;
    detail: string;
  };
}

export interface SegmentInfo {
  name: string;
  start: string;
  end: string | null;
  duration_sec: number | null;
  size_bytes: number;
}

export interface TopicPreset {
  id: string;
  name: string;
  description: string;
  icon?: string;
  recommended_cameras?: number;
  cadence_sec: number;
  window_sec: number;
  speaker_enabled: boolean;
  goal: string;
  vision_rubric?: string;
}

export interface ModelConfig {
  model: string;
  effort?: string | null;
}

export interface ReviewPolicy {
  all_clear_definition: string;
  all_clear_statuses: string[];
  digest_interval_sec: number | null;
}

export interface VisionReport {
  bundle_id: string;
  topic_id: string;
  status: ReportStatus;
  summary: string;
  observations: string[];
  conditions: string[];
  confidence: number | null;
  suggest_review: boolean;
  assets_read: string[];
  camera_status: Record<string, CameraStatus>;
  degraded: boolean;
  usage: TokenUsage;
  created_at: string;
}

export interface TickRecord {
  type: "tick";
  at: string;
  bundle_id: string;
  status: ReportStatus;
  conditions: string[];
  degraded: boolean;
  usage: TokenUsage;
  report?: VisionReport;
  available_assets?: string[];
}

export interface TopicToolInfo {
  name: string;
  path: string;
  version: string;
  code: string;
}

export interface TopicBundleInfo {
  bundle_id: string;
  saved: boolean;
  assets: string[];
}

export interface MuteState {
  mode: "none" | "timed" | "until_next_event";
  muted_at: string | null;
  muted_until: string | null;
  anchor_alert_id: string | null;
  anchor_summary: string;
  reason: string;
}

export interface TopicSummary {
  id: string;
  camera_ids: string[];
  cadence_sec: number;
  window_sec: number;
  main_model: ModelConfig;
  vision_model: ModelConfig;
  asset_kinds: string[];
  review_policy: ReviewPolicy;
  alert_defaults: Record<string, unknown>;
  paused: boolean;
  speaker_enabled: boolean;
  mute?: MuteState;
  goal: string;
  rubric_version: string;
  tool_count: number;
  ticks_total: number;
  last_tick: TickRecord | null;
  camera_health: Record<string, CameraStatus>;
}

export interface SessionInfo {
  harness?: string;
  mode?: string;
  active?: boolean;
  conversation_id?: string | null;
  model?: string;
  effort?: string | null;
  turns?: number;
  total_turns?: number;
  resets?: number;
  usage?: TokenUsage;
  cumulative_usage?: TokenUsage;
}

export interface TopicTicksResponse {
  topic_id: string;
  ticks: TickRecord[];
  reviews: Array<Record<string, unknown>>;
  actions: Array<Record<string, unknown>>;
  alerts: Array<Record<string, unknown>>;
  counters: Record<string, unknown> | null;
  token_totals: {
    vision: { input: number; cached: number; output: number; thinking: number };
    main: { input: number; cached: number; output: number; thinking: number };
  };
  session?: SessionInfo | null;
  vision_rubric: string;
  rubric_version: string;
  orchestrator_notes: string;
  tools: TopicToolInfo[];
  bundles: TopicBundleInfo[];
}

export interface ChatResponse {
  ok: boolean;
  reply: string;
  actions: ChatActionItem[];
  usage: TokenUsage;
  error: string;
}

export interface AlertFeedback {
  alert_id: string;
  rating: "up" | "down";
  note: string;
  by: string;
  at: string;
}

export interface AlertItem {
  id: string;
  request: {
    topic_id: string;
    severity: Severity;
    title: string;
    body: string;
    clip_ref: string | null;
    dedupe_key: string | null;
    cooldown_sec: number | null;
    requires_ack: boolean;
    bundle_id: string | null;
    camera_ids: string[];
    source: "main_agent" | "watchdog";
    rubric_version: string | null;
    tool_versions: Record<string, string>;
    speaker: boolean;
  };
  created_at: string;
  status: "sent" | "deduped" | "failed";
  deliveries: Record<string, string>;
  acked_at: string | null;
  acked_by: string | null;
  feedback: AlertFeedback[];
  has_clip: boolean;
  has_thumbnail: boolean;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore non-json error
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<SystemHealth>("/api/health"),
  presets: () => request<TopicPreset[]>("/api/presets"),
  cameras: () => request<CameraInfo[]>("/api/cameras"),
  cameraSegments: (cameraId: string) =>
    request<{ camera_id: string; segments: SegmentInfo[] }>(
      `/api/cameras/${encodeURIComponent(cameraId)}/segments`,
    ),
  topics: () => request<TopicSummary[]>("/api/topics"),
  topicTicks: (topicId: string, limit = 50) =>
    request<TopicTicksResponse>(
      `/api/topics/${encodeURIComponent(topicId)}/ticks?limit=${limit}`,
    ),
  createTopic: (payload: {
    id: string;
    camera_ids: string[];
    preset?: string;
    goal?: string;
    cadence_sec?: number;
    window_sec?: number;
    main_model?: ModelConfig;
    vision_model?: ModelConfig;
    speaker_enabled?: boolean;
    paused?: boolean;
  }) =>
    request<TopicSummary>("/api/topics", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateTopic: (
    topicId: string,
    payload: {
      camera_ids?: string[];
      cadence_sec?: number;
      window_sec?: number;
      main_model?: ModelConfig;
      vision_model?: ModelConfig;
      paused?: boolean;
      speaker_enabled?: boolean;
      goal?: string;
    },
  ) =>
    request<TopicSummary>(`/api/topics/${encodeURIComponent(topicId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  setTopicMute: (
    topicId: string,
    payload: {
      mode: "none" | "timed" | "until_next_event";
      duration_min?: number;
      anchor_alert_id?: string;
      anchor_summary?: string;
      reason?: string;
    },
  ) =>
    request<TopicSummary>(`/api/topics/${encodeURIComponent(topicId)}/mute`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  chatTopic: (topicId: string, message: string, attachments?: string[]) =>
    request<ChatResponse>(`/api/topics/${encodeURIComponent(topicId)}/chat`, {
      method: "POST",
      body: JSON.stringify({
        message,
        attachments: attachments && attachments.length > 0 ? attachments : undefined,
      }),
    }),
  alerts: (params?: { topic_id?: string; severity?: string; unacked?: boolean; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.topic_id && params.topic_id !== "all") qs.set("topic_id", params.topic_id);
    if (params?.severity && params.severity !== "all") qs.set("severity", params.severity);
    if (params?.unacked) qs.set("unacked", "true");
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return request<AlertItem[]>(`/api/alerts${q ? `?${q}` : ""}`);
  },
  ackAlert: (alertId: string, by = "web-ui") =>
    request<AlertItem>(`/api/alerts/${encodeURIComponent(alertId)}/ack`, {
      method: "POST",
      body: JSON.stringify({ by }),
    }),
  feedbackAlert: (alertId: string, rating: "up" | "down", note = "", by = "web-ui") =>
    request<{ feedback: AlertFeedback; pinned: boolean; alert: AlertItem }>(
      `/api/alerts/${encodeURIComponent(alertId)}/feedback`,
      {
        method: "POST",
        body: JSON.stringify({ rating, note, by }),
      },
    ),

  // URL builders
  cameraLiveMp4Url: (cameraId: string) =>
    `/api/cameras/${encodeURIComponent(cameraId)}/live.mp4`,
  cameraStreamUrl: (cameraId: string, fps = 2) =>
    `/api/cameras/${encodeURIComponent(cameraId)}/stream?fps=${fps}`,
  cameraSnapshotUrl: (cameraId: string, cacheBust?: number) =>
    `/api/cameras/${encodeURIComponent(cameraId)}/snapshot.jpg${cacheBust ? `?t=${cacheBust}` : ""}`,
  cameraSegmentUrl: (cameraId: string, segmentName: string) =>
    `/api/cameras/${encodeURIComponent(cameraId)}/segments/${encodeURIComponent(segmentName)}`,
  cameraClipUrl: (cameraId: string, seconds: number, cacheBust?: number) =>
    `/api/cameras/${encodeURIComponent(cameraId)}/clip?seconds=${seconds}${cacheBust ? `&t=${cacheBust}` : ""}`,
  bundleAssetUrl: (topicId: string, bundleId: string, assetPath: string) =>
    `/api/topics/${encodeURIComponent(topicId)}/bundles/${encodeURIComponent(bundleId)}/${assetPath
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`,
  alertThumbnailUrl: (alertId: string) =>
    `/api/alerts/${encodeURIComponent(alertId)}/thumbnail`,
  alertClipUrl: (alertId: string) =>
    `/api/alerts/${encodeURIComponent(alertId)}/clip`,
};
