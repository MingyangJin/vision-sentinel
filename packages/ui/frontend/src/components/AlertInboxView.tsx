import { useState } from "react";
import {
  Bell,
  BellOff,
  BellRing,
  CheckCheck,
  Clock,
  GraduationCap,
  RefreshCw,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Volume2,
} from "lucide-react";
import {
  api,
  type AlertItem,
  type MuteState,
  type Severity,
  type TopicSummary,
} from "@/lib/api";
import type { AlertFilterState } from "@/lib/storage";
import { cn, formatRelativeTime } from "@/lib/utils";
import type { TeachContext } from "./TopicsWorkspaceView";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent } from "./ui/card";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Switch } from "./ui/switch";

interface AlertInboxViewProps {
  alerts: AlertItem[];
  topics: TopicSummary[];
  filter: AlertFilterState;
  onFilterChange: (next: AlertFilterState) => void;
  onRefresh: () => void;
  onTeachAgent: (ctx: TeachContext) => void;
  onToast: (msg: string, variant?: "default" | "error") => void;
}

function severityVariant(sev: Severity): "danger" | "warning" | "info" {
  if (sev === "urgent") return "danger";
  if (sev === "warning") return "warning";
  return "info";
}

function formatMuteLabel(mute?: MuteState): string | null {
  if (!mute || mute.mode === "none") return null;
  if (mute.mode === "until_next_event") return "Muted until next event (AI)";
  if (mute.muted_until) {
    const ms = new Date(mute.muted_until).getTime() - Date.now();
    if (ms <= 0) return null;
    const mins = Math.max(1, Math.ceil(ms / 60000));
    if (mins >= 60) {
      const hrs = Math.floor(mins / 60);
      const rem = mins % 60;
      return rem > 0 ? `Muted · ${hrs}h ${rem}m left` : `Muted · ${hrs}h left`;
    }
    return `Muted · ${mins}m left`;
  }
  return "Muted";
}

export function AlertInboxView({
  alerts,
  topics,
  filter,
  onFilterChange,
  onRefresh,
  onTeachAgent,
  onToast,
}: AlertInboxViewProps) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [muteOpenAlertId, setMuteOpenAlertId] = useState<string | null>(null);
  const [customMinutes, setCustomMinutes] = useState("45");
  const [feedbackOpen, setFeedbackOpen] = useState<{
    alertId: string;
    rating: "up" | "down";
    note: string;
  } | null>(null);

  const topicById = new Map(topics.map((t) => [t.id, t]));
  const mutedTopics = topics.filter((t) => Boolean(formatMuteLabel(t.mute)));

  const handleSetMute = async (
    topicId: string,
    payload: {
      mode: "none" | "timed" | "until_next_event";
      duration_min?: number;
      anchor_alert_id?: string;
      anchor_summary?: string;
    },
    label: string,
  ) => {
    setBusyId(topicId);
    try {
      await api.setTopicMute(topicId, payload);
      setMuteOpenAlertId(null);
      onRefresh();
      onToast(label);
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Failed to update mute", "error");
    } finally {
      setBusyId(null);
    }
  };

  const filtered = alerts.filter((a) => {
    if (filter.severity !== "all" && a.request.severity !== filter.severity) return false;
    if (filter.unackedOnly && a.acked_at !== null) return false;
    if (filter.topicId !== "all" && a.request.topic_id !== filter.topicId) return false;
    return true;
  });

  const handleAck = async (alertId: string) => {
    setBusyId(alertId);
    try {
      await api.ackAlert(alertId);
      onRefresh();
      onToast("Alert acknowledged");
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Ack failed", "error");
    } finally {
      setBusyId(null);
    }
  };

  const handleSubmitFeedback = async (alert: AlertItem, rating: "up" | "down", note: string) => {
    setBusyId(alert.id);
    try {
      const res = await api.feedbackAlert(alert.id, rating, note);
      setFeedbackOpen(null);
      onRefresh();
      onToast(
        rating === "up"
          ? `Marked true positive${res.pinned ? " · bundle saved" : ""}`
          : `Marked false alarm${res.pinned ? " · bundle saved" : ""}`,
      );
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Feedback failed", "error");
    } finally {
      setBusyId(null);
    }
  };

  const handleTeach = (alert: AlertItem) => {
    const latestFb = alert.feedback[alert.feedback.length - 1];
    const fbSummary = latestFb
      ? `\nHuman feedback: ${latestFb.rating === "down" ? "Thumbs Down (False Alarm)" : "Thumbs Up (True Positive)"}${latestFb.note ? ` — "${latestFb.note}"` : ""}`
      : "";
    const bundlePart = alert.request.bundle_id ? `\nBundle ID: ${alert.request.bundle_id}` : "";
    const draft = [
      `Review alert [${alert.request.severity.toUpperCase()}] "${alert.request.title}" (${alert.created_at}).`,
      alert.request.body ? `Alert details: ${alert.request.body}` : "",
      bundlePart.trim(),
      fbSummary.trim(),
      "",
      latestFb?.rating === "down"
        ? "This was a false alarm. Please inspect the bundle, explain why it triggered, and update the vision rubric or helper tools so it does not recur."
        : "Please review this alert and confirm whether the vision rubric or cadence should be tuned.",
    ]
      .filter(Boolean)
      .join("\n");

    const attachments: string[] = [];
    if (alert.request.bundle_id) {
      if (alert.request.clip_ref) {
        attachments.push(`${alert.request.bundle_id}/${alert.request.clip_ref}`);
      } else {
        attachments.push(alert.request.bundle_id);
      }
    }

    onTeachAgent({
      topicId: alert.request.topic_id,
      draft,
      attachments,
      label: `Alert "${alert.request.title}"${alert.request.bundle_id ? ` · ${alert.request.bundle_id}` : ""}`,
    });
  };

  const unackedCount = alerts.filter((a) => !a.acked_at).length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Alert Inbox</h1>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
            <span>{alerts.length} total</span>
            {unackedCount > 0 && <Badge variant="warning">{unackedCount} unacknowledged</Badge>}
          </div>
        </div>
        <Button size="sm" variant="outline" onClick={onRefresh}>
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      {/* Filter Toolbar (persisted in localStorage) */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 p-3">
          <div className="w-36">
            <Select
              value={filter.severity}
              onValueChange={(v) =>
                onFilterChange({
                  ...filter,
                  severity: v as AlertFilterState["severity"],
                })
              }
            >
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="Severity" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Severities</SelectItem>
                <SelectItem value="urgent">Urgent</SelectItem>
                <SelectItem value="warning">Warning</SelectItem>
                <SelectItem value="info">Info</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="w-44">
            <Select
              value={filter.topicId}
              onValueChange={(v) => onFilterChange({ ...filter, topicId: v })}
            >
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="Topic" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Topics</SelectItem>
                {topics.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <label className="inline-flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
            <Switch
              checked={filter.unackedOnly}
              onCheckedChange={(checked) =>
                onFilterChange({ ...filter, unackedOnly: checked })
              }
            />
            Unacknowledged only
          </label>
        </CardContent>
      </Card>

      {/* Active Mutes Banner */}
      {mutedTopics.length > 0 && (
        <div className="space-y-2">
          {mutedTopics.map((t) => {
            const label = formatMuteLabel(t.mute);
            return (
              <div
                key={t.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs"
              >
                <div className="flex flex-wrap items-center gap-2 min-w-0">
                  <BellOff className="h-4 w-4 shrink-0 text-amber-400" />
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    {t.id}
                  </Badge>
                  <span className="font-medium text-amber-200">{label}</span>
                  {t.mute?.mode === "until_next_event" && t.mute.anchor_summary && (
                    <span className="truncate text-muted-foreground max-w-md">
                      · anchored on: {t.mute.anchor_summary}
                    </span>
                  )}
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs border-amber-500/40 hover:bg-amber-500/20"
                  disabled={busyId === t.id}
                  onClick={() =>
                    void handleSetMute(t.id, { mode: "none" }, `Unmuted ${t.id}`)
                  }
                >
                  Unmute
                </Button>
              </div>
            );
          })}
        </div>
      )}

      {/* Alert List */}
      {filtered.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-14 text-center">
            <Bell className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm font-medium">No matching alerts</p>
            <p className="text-xs text-muted-foreground">
              Alerts approved by your Topics&apos; Main Agents (or Watchdog fallback alerts) appear
              here with one-click acknowledgment and feedback.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {filtered.map((alert) => {
            const isBusy = busyId === alert.id;
            const isAcked = Boolean(alert.acked_at);
            const fbDraft =
              feedbackOpen?.alertId === alert.id ? feedbackOpen : null;
            const latestRating = alert.feedback[alert.feedback.length - 1]?.rating;

            return (
              <Card
                key={alert.id}
                className={cn(
                  "overflow-hidden transition-colors",
                  !isAcked &&
                    alert.request.severity === "urgent" &&
                    "border-rose-500/40",
                )}
              >
                <CardContent className="p-4">
                  <div className="flex flex-col gap-4 lg:flex-row">
                    {/* Media Video (preferred) / Thumbnail Fallback Column */}
                    {(alert.has_clip || alert.has_thumbnail) && (
                      <div className="w-full shrink-0 space-y-2 lg:w-80">
                        {alert.has_clip ? (
                          <video
                            src={api.alertClipUrl(alert.id)}
                            poster={
                              alert.has_thumbnail
                                ? api.alertThumbnailUrl(alert.id)
                                : undefined
                            }
                            controls
                            autoPlay
                            muted
                            loop
                            playsInline
                            preload="metadata"
                            className="aspect-video w-full rounded-lg border border-border bg-black object-cover"
                          />
                        ) : (
                          <div className="relative overflow-hidden rounded-lg border border-border bg-black">
                            <img
                              src={api.alertThumbnailUrl(alert.id)}
                              alt={alert.request.title}
                              className="aspect-video w-full object-cover"
                            />
                          </div>
                        )}
                      </div>
                    )}

                    {/* Main Details & Actions Column */}
                    <div className="flex flex-1 flex-col justify-between gap-3 min-w-0">
                      <div className="space-y-1.5">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <Badge variant={severityVariant(alert.request.severity)}>
                              <BellRing className="h-3 w-3" />
                              {alert.request.severity}
                            </Badge>
                            <Badge variant="secondary" className="font-mono">
                              {alert.request.topic_id}
                            </Badge>
                            {alert.request.camera_ids.map((cid) => (
                              <Badge key={cid} variant="outline" className="font-mono text-[10px]">
                                {cid}
                              </Badge>
                            ))}
                            {alert.request.speaker && (
                              <Badge variant="warning" className="text-[10px]">
                                <Volume2 className="h-3 w-3" />
                                speaker
                              </Badge>
                            )}
                            {isAcked ? (
                              <Badge variant="success">
                                <CheckCheck className="h-3 w-3" />
                                Acked
                              </Badge>
                            ) : (
                              <Badge variant="outline">Unacked</Badge>
                            )}
                          </div>
                          <span
                            className="text-[11px] text-muted-foreground tabular-nums"
                            title={alert.created_at}
                          >
                            {formatRelativeTime(alert.created_at)}
                          </span>
                        </div>

                        <h3 className="text-sm font-semibold">{alert.request.title}</h3>
                        {alert.request.body && (
                          <p className="text-xs leading-relaxed text-muted-foreground">
                            {alert.request.body}
                          </p>
                        )}

                        {alert.request.bundle_id && (
                          <div className="text-[11px] text-muted-foreground font-mono">
                            bundle: {alert.request.bundle_id}
                            {alert.request.dedupe_key ? ` · key: ${alert.request.dedupe_key}` : ""}
                          </div>
                        )}

                        {/* Recorded feedback badges */}
                        {alert.feedback.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 pt-1">
                            {alert.feedback.map((fb, idx) => (
                              <Badge
                                key={idx}
                                variant={fb.rating === "up" ? "success" : "danger"}
                                className="text-[10px]"
                              >
                                {fb.rating === "up" ? "True positive" : "False alarm"}
                                {fb.note ? `: ${fb.note}` : ""}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Action Bar */}
                      <div className="space-y-2 border-t border-border/70 pt-2.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <Button
                            size="sm"
                            variant={isAcked ? "secondary" : "default"}
                            disabled={isAcked || isBusy}
                            onClick={() => void handleAck(alert.id)}
                          >
                            <CheckCheck className="h-3.5 w-3.5" />
                            {isAcked ? "Acknowledged" : "Acknowledge"}
                          </Button>

                          <Button
                            size="sm"
                            variant={latestRating === "up" ? "secondary" : "outline"}
                            disabled={isBusy}
                            onClick={() =>
                              setFeedbackOpen(
                                fbDraft?.rating === "up"
                                  ? null
                                  : { alertId: alert.id, rating: "up", note: "" },
                              )
                            }
                            title="Rate true positive"
                          >
                            <ThumbsUp className="h-3.5 w-3.5 text-emerald-400" />
                            True Positive
                          </Button>

                          <Button
                            size="sm"
                            variant={latestRating === "down" ? "secondary" : "outline"}
                            disabled={isBusy}
                            onClick={() =>
                              setFeedbackOpen(
                                fbDraft?.rating === "down"
                                  ? null
                                  : { alertId: alert.id, rating: "down", note: "" },
                              )
                            }
                            title="Rate false alarm"
                          >
                            <ThumbsDown className="h-3.5 w-3.5 text-rose-400" />
                            False Alarm
                          </Button>

                          <Button
                            size="sm"
                            variant="outline"
                            className="border-primary/40 text-primary hover:bg-primary/10"
                            onClick={() => handleTeach(alert)}
                          >
                            <GraduationCap className="h-3.5 w-3.5" />
                            Teach Agent
                          </Button>

                          {(() => {
                            const topicMute = topicById.get(alert.request.topic_id)?.mute;
                            const activeLabel = formatMuteLabel(topicMute);
                            if (activeLabel) {
                              return (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="border-amber-500/40 text-amber-300 hover:bg-amber-500/15"
                                  disabled={busyId === alert.request.topic_id}
                                  onClick={() =>
                                    void handleSetMute(
                                      alert.request.topic_id,
                                      { mode: "none" },
                                      `Unmuted ${alert.request.topic_id}`,
                                    )
                                  }
                                >
                                  <BellOff className="h-3.5 w-3.5" />
                                  {activeLabel} · Unmute
                                </Button>
                              );
                            }
                            return (
                              <Button
                                size="sm"
                                variant={muteOpenAlertId === alert.id ? "secondary" : "outline"}
                                disabled={busyId === alert.request.topic_id}
                                onClick={() =>
                                  setMuteOpenAlertId(
                                    muteOpenAlertId === alert.id ? null : alert.id,
                                  )
                                }
                              >
                                <BellOff className="h-3.5 w-3.5" />
                                Mute
                              </Button>
                            );
                          })()}
                        </div>

                        {/* Mute options drawer (timed or AI-decided until next event) */}
                        {muteOpenAlertId === alert.id && (
                          <div className="space-y-2 rounded-lg border border-amber-500/30 bg-muted/40 p-2.5">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <Button
                                size="sm"
                                variant="default"
                                className="h-7 text-xs bg-amber-600 hover:bg-amber-500 text-white"
                                disabled={busyId === alert.request.topic_id}
                                onClick={() =>
                                  void handleSetMute(
                                    alert.request.topic_id,
                                    {
                                      mode: "until_next_event",
                                      anchor_alert_id: alert.id,
                                      anchor_summary: `${alert.request.title}: ${alert.request.body}`,
                                    },
                                    `Muted ${alert.request.topic_id} until next event (AI)`,
                                  )
                                }
                              >
                                <Sparkles className="h-3.5 w-3.5" />
                                Until next event (AI)
                              </Button>
                              {[
                                { label: "15m", mins: 15 },
                                { label: "30m", mins: 30 },
                                { label: "1h", mins: 60 },
                                { label: "4h", mins: 240 },
                              ].map((opt) => (
                                <Button
                                  key={opt.label}
                                  size="sm"
                                  variant="outline"
                                  className="h-7 text-xs"
                                  disabled={busyId === alert.request.topic_id}
                                  onClick={() =>
                                    void handleSetMute(
                                      alert.request.topic_id,
                                      {
                                        mode: "timed",
                                        duration_min: opt.mins,
                                        anchor_alert_id: alert.id,
                                      },
                                      `Muted ${alert.request.topic_id} for ${opt.label}`,
                                    )
                                  }
                                >
                                  <Clock className="h-3 w-3" />
                                  {opt.label}
                                </Button>
                              ))}
                              <form
                                className="inline-flex items-center gap-1"
                                onSubmit={(e) => {
                                  e.preventDefault();
                                  const m = Number(customMinutes);
                                  if (!Number.isFinite(m) || m <= 0) return;
                                  void handleSetMute(
                                    alert.request.topic_id,
                                    {
                                      mode: "timed",
                                      duration_min: m,
                                      anchor_alert_id: alert.id,
                                    },
                                    `Muted ${alert.request.topic_id} for ${m}m`,
                                  );
                                }}
                              >
                                <Input
                                  type="number"
                                  min={1}
                                  max={10080}
                                  value={customMinutes}
                                  onChange={(e) => setCustomMinutes(e.target.value)}
                                  className="h-7 w-16 text-xs"
                                  aria-label="Custom mute minutes"
                                />
                                <Button
                                  size="sm"
                                  type="submit"
                                  variant="outline"
                                  className="h-7 text-xs"
                                  disabled={busyId === alert.request.topic_id}
                                >
                                  min
                                </Button>
                              </form>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-7 text-xs"
                                onClick={() => setMuteOpenAlertId(null)}
                              >
                                Cancel
                              </Button>
                            </div>
                            <p className="text-[11px] text-muted-foreground">
                              <strong>Until next event (AI)</strong> tells the Main Agent to stay
                              quiet for continuations of this episode and automatically unmute +
                              alert when it judges a genuinely new event begins.
                            </p>
                          </div>
                        )}

                        {/* Optional feedback note composer */}
                        {fbDraft && (
                          <form
                            onSubmit={(e) => {
                              e.preventDefault();
                              void handleSubmitFeedback(alert, fbDraft.rating, fbDraft.note);
                            }}
                            className="flex flex-col gap-2 rounded-lg border border-border bg-muted/40 p-2.5 sm:flex-row"
                          >
                            <Input
                              value={fbDraft.note}
                              onChange={(e) =>
                                setFeedbackOpen({ ...fbDraft, note: e.target.value })
                              }
                              placeholder={
                                fbDraft.rating === "down"
                                  ? "Optional note (e.g., tree shadow on left)…"
                                  : "Optional confirmation note…"
                              }
                              className="h-8 text-xs"
                              autoFocus
                            />
                            <div className="flex gap-1.5">
                              <Button size="sm" type="submit" disabled={isBusy}>
                                Save {fbDraft.rating === "up" ? "Thumbs Up" : "Thumbs Down"}
                              </Button>
                              <Button
                                size="sm"
                                type="button"
                                variant="ghost"
                                onClick={() => setFeedbackOpen(null)}
                              >
                                Cancel
                              </Button>
                            </div>
                          </form>
                        )}
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
