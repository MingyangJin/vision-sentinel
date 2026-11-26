import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  Bot,
  Check,
  Code2,
  Cpu,
  FileText,
  Film,
  Image as ImageIcon,
  Layers,
  MessageSquare,
  Paperclip,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Send,
  Settings2,
  ShieldAlert,
  Sparkles,
  Trash2,
  Volume2,
  X,
} from "lucide-react";
import {
  api,
  type CameraInfo,
  type ReportStatus,
  type TickRecord,
  type TopicPreset,
  type TopicSummary,
  type TopicTicksResponse,
} from "@/lib/api";
import type { ChatMessage, StreamMode, TopicSubTab } from "@/lib/storage";
import { cn, formatRelativeTime, formatShortTime, formatTokens } from "@/lib/utils";
import { CameraFeed } from "./CameraFeed";
import { CreateTopicDialog } from "./CreateTopicDialog";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { Input, Label, Textarea } from "./ui/input";
import { Switch } from "./ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

export interface TeachContext {
  topicId: string;
  draft: string;
  attachments: string[];
  label: string;
}

interface TopicsWorkspaceViewProps {
  topics: TopicSummary[];
  cameras: CameraInfo[];
  presets: TopicPreset[];
  selectedTopicId: string | null;
  onSelectTopic: (id: string) => void;
  topicSubTab: TopicSubTab;
  onTopicSubTabChange: (tab: TopicSubTab) => void;
  streamMode: StreamMode;
  chatDrafts: Record<string, string>;
  onChatDraftChange: (topicId: string, draft: string) => void;
  chatHistory: Record<string, ChatMessage[]>;
  onChatHistoryChange: (topicId: string, messages: ChatMessage[]) => void;
  teachContext: TeachContext | null;
  onConsumeTeachContext: () => void;
  onTopicsChanged: () => void;
  onToast: (msg: string, variant?: "default" | "error") => void;
}

function statusBadgeVariant(status?: ReportStatus | null): "success" | "warning" | "danger" | "outline" {
  if (status === "clear") return "success";
  if (status === "notable") return "warning";
  if (status === "urgent" || status === "error") return "danger";
  return "outline";
}

export function TopicsWorkspaceView({
  topics,
  cameras,
  presets,
  selectedTopicId,
  onSelectTopic,
  topicSubTab,
  onTopicSubTabChange,
  streamMode,
  chatDrafts,
  onChatDraftChange,
  chatHistory,
  onChatHistoryChange,
  teachContext,
  onConsumeTeachContext,
  onTopicsChanged,
  onToast,
}: TopicsWorkspaceViewProps) {
  const [createOpen, setCreateOpen] = useState(false);
  const [ticksData, setTicksData] = useState<TopicTicksResponse | null>(null);
  const [loadingTicks, setLoadingTicks] = useState(false);
  const [previewAsset, setPreviewAsset] = useState<{
    topicId: string;
    bundleId: string;
    asset: string;
  } | null>(null);

  // Mid-flow editable settings form state
  const [editGoal, setEditGoal] = useState("");
  const [editCameras, setEditCameras] = useState<string[]>([]);
  const [editCadence, setEditCadence] = useState(60);
  const [editWindow, setEditWindow] = useState(20);
  const [editMainModel, setEditMainModel] = useState("");
  const [editMainEffort, setEditMainEffort] = useState("");
  const [editVisionModel, setEditVisionModel] = useState("");
  const [editVisionEffort, setEditVisionEffort] = useState("");
  const [editPaused, setEditPaused] = useState(false);
  const [editSpeaker, setEditSpeaker] = useState(false);
  const [savingConfig, setSavingConfig] = useState(false);
  const [dirtyConfig, setDirtyConfig] = useState(false);

  // Chat state
  const [pendingAttachments, setPendingAttachments] = useState<string[]>([]);
  const [attachmentLabel, setAttachmentLabel] = useState<string | null>(null);
  const [sendingChat, setSendingChat] = useState(false);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  const selectedTopic =
    topics.find((t) => t.id === selectedTopicId) ?? topics[0] ?? null;
  const activeTopicId = selectedTopic?.id ?? null;

  // Sync editable form when selected Topic changes
  useEffect(() => {
    if (!selectedTopic) return;
    setEditGoal(selectedTopic.goal);
    setEditCameras(selectedTopic.camera_ids);
    setEditCadence(selectedTopic.cadence_sec);
    setEditWindow(selectedTopic.window_sec);
    setEditMainModel(selectedTopic.main_model.model);
    setEditMainEffort(selectedTopic.main_model.effort ?? "medium");
    setEditVisionModel(selectedTopic.vision_model.model);
    setEditVisionEffort(selectedTopic.vision_model.effort ?? "low");
    setEditPaused(selectedTopic.paused);
    setEditSpeaker(selectedTopic.speaker_enabled);
    setDirtyConfig(false);
  }, [
    selectedTopic?.id,
    selectedTopic?.goal,
    selectedTopic?.cadence_sec,
    selectedTopic?.window_sec,
    selectedTopic?.paused,
    selectedTopic?.speaker_enabled,
    selectedTopic?.camera_ids.join(","),
    selectedTopic?.main_model.model,
    selectedTopic?.vision_model.model,
  ]);

  // Apply "Teach Agent" handoff from Alert Inbox
  useEffect(() => {
    if (!teachContext) return;
    onSelectTopic(teachContext.topicId);
    onChatDraftChange(teachContext.topicId, teachContext.draft);
    setPendingAttachments(teachContext.attachments);
    setAttachmentLabel(teachContext.label);
    onConsumeTeachContext();
  }, [teachContext, onSelectTopic, onChatDraftChange, onConsumeTeachContext]);

  const loadTicks = useCallback(async (tid: string) => {
    setLoadingTicks(true);
    try {
      const data = await api.topicTicks(tid, 50);
      setTicksData(data);
    } catch {
      setTicksData(null);
    } finally {
      setLoadingTicks(false);
    }
  }, []);

  useEffect(() => {
    if (!activeTopicId) {
      setTicksData(null);
      return;
    }
    void loadTicks(activeTopicId);
    const id = window.setInterval(() => void loadTicks(activeTopicId), 10_000);
    return () => window.clearInterval(id);
  }, [activeTopicId, loadTicks]);

  const messages = activeTopicId ? chatHistory[activeTopicId] ?? [] : [];
  const draft = activeTopicId ? chatDrafts[activeTopicId] ?? "" : "";

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, sendingChat]);

  const handleQuickTogglePause = async () => {
    if (!selectedTopic) return;
    try {
      const next = !selectedTopic.paused;
      await api.updateTopic(selectedTopic.id, { paused: next });
      onTopicsChanged();
      onToast(next ? `Paused topic "${selectedTopic.id}"` : `Resumed topic "${selectedTopic.id}"`);
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Failed to toggle pause", "error");
    }
  };

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTopic) return;
    if (editCameras.length === 0) {
      onToast("Select at least one camera for this Topic", "error");
      return;
    }
    setSavingConfig(true);
    try {
      await api.updateTopic(selectedTopic.id, {
        camera_ids: editCameras,
        cadence_sec: editCadence,
        window_sec: editWindow,
        main_model: { model: editMainModel.trim(), effort: editMainEffort || null },
        vision_model: { model: editVisionModel.trim(), effort: editVisionEffort || null },
        paused: editPaused,
        speaker_enabled: editSpeaker,
        goal: editGoal,
      });
      setDirtyConfig(false);
      onTopicsChanged();
      void loadTicks(selectedTopic.id);
      onToast(`Saved settings & goal.md for "${selectedTopic.id}"`);
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Failed to save settings", "error");
    } finally {
      setSavingConfig(false);
    }
  };

  const handleSendChat = async (overrideMessage?: string) => {
    if (!activeTopicId) return;
    const text = (overrideMessage ?? draft).trim();
    if (!text || sendingChat) return;

    const userMsg: ChatMessage = {
      id: `${Date.now()}-u`,
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
      attachments: pendingAttachments.length > 0 ? [...pendingAttachments] : undefined,
    };
    const nextList = [...messages, userMsg];
    onChatHistoryChange(activeTopicId, nextList);
    onChatDraftChange(activeTopicId, "");
    const usedAttachments = [...pendingAttachments];
    setPendingAttachments([]);
    setAttachmentLabel(null);
    setSendingChat(true);

    try {
      const res = await api.chatTopic(activeTopicId, text, usedAttachments);
      const replyMsg: ChatMessage = {
        id: `${Date.now()}-a`,
        role: res.ok ? "assistant" : "error",
        content: res.ok ? res.reply || "(Main Agent returned no text)" : res.error || "Chat turn failed",
        timestamp: new Date().toISOString(),
        actions: res.actions,
        usage: res.usage,
      };
      onChatHistoryChange(activeTopicId, [...nextList, replyMsg]);
      onTopicsChanged();
      void loadTicks(activeTopicId);
    } catch (err) {
      const errMsg: ChatMessage = {
        id: `${Date.now()}-e`,
        role: "error",
        content: err instanceof Error ? err.message : "Failed to reach Main Agent",
        timestamp: new Date().toISOString(),
      };
      onChatHistoryChange(activeTopicId, [...nextList, errMsg]);
    } finally {
      setSendingChat(false);
    }
  };

  const subscribedCameras = selectedTopic
    ? selectedTopic.camera_ids
        .map((cid) => cameras.find((c) => c.camera_id === cid))
        .filter((c): c is CameraInfo => Boolean(c))
    : [];

  return (
    <div className="space-y-4">
      {/* Top Topic Selector Strip */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Topics & Main Agent Chat</h1>
          <p className="text-xs text-muted-foreground">
            Manage M:N camera topics, edit human-owned <code className="font-mono">goal.md</code>,
            inspect Vision Sub-Agent ticks, and tune your Main Agent live.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)} size="sm">
          <Plus className="h-4 w-4" />
          New Topic
        </Button>
      </div>

      {/* Horizontal Topic Picker Pills */}
      <div className="flex flex-wrap items-center gap-2">
        {topics.map((t) => {
          const active = t.id === activeTopicId;
          const lastStatus = t.last_tick?.status ?? null;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => onSelectTopic(t.id)}
              className={cn(
                "flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-all cursor-pointer",
                active
                  ? "border-primary bg-primary/12 text-foreground shadow-2xs"
                  : "border-border bg-card text-muted-foreground hover:border-primary/40 hover:text-foreground",
              )}
            >
              <span
                className={cn(
                  "h-2 w-2 rounded-full",
                  t.paused ? "bg-slate-500" : "bg-emerald-400",
                )}
              />
              <span className="font-mono font-semibold text-foreground">{t.id}</span>
              <span className="text-[11px] text-muted-foreground">
                {t.camera_ids.length} cam{t.camera_ids.length === 1 ? "" : "s"}
              </span>
              {t.paused ? (
                <Badge variant="outline" className="text-[10px]">
                  paused
                </Badge>
              ) : (
                lastStatus && (
                  <Badge variant={statusBadgeVariant(lastStatus)} className="text-[10px]">
                    {lastStatus}
                  </Badge>
                )
              )}
            </button>
          );
        })}
        {topics.length === 0 && (
          <Card className="w-full">
            <CardContent className="flex flex-col items-center justify-center gap-3 py-12 text-center">
              <Sparkles className="h-8 w-8 text-primary/70" />
              <div>
                <p className="text-sm font-medium">No monitoring Topics yet</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Create your first Topic from a preset (Package Watch, Door Left Open, Pet Watch,
                  or Multi-Cam Watch).
                </p>
              </div>
              <Button onClick={() => setCreateOpen(true)} size="sm">
                <Plus className="h-4 w-4" />
                Create First Topic
              </Button>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Main Split Workspace */}
      {selectedTopic && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
          {/* LEFT PANE: Subscribed Cameras + Ticks / Config / State */}
          <div className="space-y-4 xl:col-span-7">
            {/* Topic Summary Header + Quick Pause/Resume */}
            <Card>
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <CardTitle className="font-mono text-base">{selectedTopic.id}</CardTitle>
                      <Badge variant={selectedTopic.paused ? "outline" : "success"}>
                        {selectedTopic.paused ? "Paused" : "Running"}
                      </Badge>
                      {selectedTopic.speaker_enabled && (
                        <Badge variant="warning">
                          <Volume2 className="h-3 w-3" />
                          Speaker opt-in
                        </Badge>
                      )}
                    </div>
                    <CardDescription className="line-clamp-2">
                      {selectedTopic.goal.trim()}
                    </CardDescription>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant={selectedTopic.paused ? "default" : "outline"}
                      onClick={handleQuickTogglePause}
                    >
                      {selectedTopic.paused ? (
                        <>
                          <Play className="h-3.5 w-3.5" />
                          Resume
                        </>
                      ) : (
                        <>
                          <Pause className="h-3.5 w-3.5" />
                          Pause
                        </>
                      )}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void loadTicks(selectedTopic.id)}
                      disabled={loadingTicks}
                    >
                      <RefreshCw className={cn("h-3.5 w-3.5", loadingTicks && "animate-spin")} />
                    </Button>
                  </div>
                </div>
              </CardHeader>

              {/* Subscribed Live Camera Strip */}
              {subscribedCameras.length > 0 && (
                <CardContent className="pt-0">
                  <div
                    className={cn(
                      "grid gap-2.5",
                      subscribedCameras.length === 1
                        ? "grid-cols-1"
                        : "grid-cols-1 sm:grid-cols-2",
                    )}
                  >
                    {subscribedCameras.map((cam) => (
                      <CameraFeed
                        key={cam.camera_id}
                        camera={cam}
                        streamMode={streamMode}
                        compact
                      />
                    ))}
                  </div>
                </CardContent>
              )}
            </Card>

            {/* Tabbed Inspector: Vision Ticks | Goal & Settings | Rubric & Tools */}
            <Tabs
              value={topicSubTab}
              onValueChange={(v) => onTopicSubTabChange(v as TopicSubTab)}
            >
              <TabsList className="w-full justify-start overflow-x-auto">
                <TabsTrigger value="ticks">
                  <Activity className="h-3.5 w-3.5" />
                  Vision Ticks ({ticksData?.ticks.length ?? 0})
                </TabsTrigger>
                <TabsTrigger value="config">
                  <Settings2 className="h-3.5 w-3.5" />
                  Goal & Settings
                  {dirtyConfig && (
                    <span className="ml-1 h-1.5 w-1.5 rounded-full bg-amber-400" />
                  )}
                </TabsTrigger>
                <TabsTrigger value="state">
                  <Code2 className="h-3.5 w-3.5" />
                  Rubric, Notes & Tools ({ticksData?.tools.length ?? selectedTopic.tool_count})
                </TabsTrigger>
              </TabsList>

              {/* TAB 1: Vision Sub-Agent Tick Feed */}
              <TabsContent value="ticks" className="space-y-3">
                {/* Token usage summary bar */}
                {ticksData && (
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <MetricPill
                      label="Vision Input"
                      value={formatTokens(ticksData.token_totals.vision.input)}
                      sub={`${formatTokens(ticksData.token_totals.vision.cached)} cached`}
                    />
                    <MetricPill
                      label="Vision Output"
                      value={formatTokens(ticksData.token_totals.vision.output)}
                      sub={`${formatTokens(ticksData.token_totals.vision.thinking)} think`}
                    />
                    <MetricPill
                      label="Main Input"
                      value={formatTokens(ticksData.token_totals.main.input)}
                      sub={`${formatTokens(ticksData.token_totals.main.cached)} cached`}
                    />
                    <MetricPill
                      label="Schedule"
                      value={`${selectedTopic.cadence_sec}s`}
                      sub={`${selectedTopic.window_sec}s window`}
                    />
                  </div>
                )}

                {!ticksData || ticksData.ticks.length === 0 ? (
                  <Card>
                    <CardContent className="py-10 text-center text-xs text-muted-foreground">
                      No Vision Sub-Agent ticks recorded yet for{" "}
                      <span className="font-mono">{selectedTopic.id}</span>.
                    </CardContent>
                  </Card>
                ) : (
                  <div className="space-y-2.5 max-h-[560px] overflow-y-auto pr-1">
                    {ticksData.ticks.map((tick: TickRecord) => {
                      const summary = tick.report?.summary || "(no summary)";
                      const observations = tick.report?.observations ?? [];
                      const assets = tick.available_assets ?? [];
                      return (
                        <Card key={`${tick.bundle_id}-${tick.at}`}>
                          <CardContent className="space-y-2 p-3.5">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <Badge variant={statusBadgeVariant(tick.status)}>
                                  {tick.status}
                                </Badge>
                                {tick.degraded && <Badge variant="warning">degraded</Badge>}
                                {tick.report?.suggest_review && (
                                  <Badge variant="info">review requested</Badge>
                                )}
                                <span className="font-mono text-[11px] text-muted-foreground">
                                  {tick.bundle_id}
                                </span>
                              </div>
                              <div className="flex items-center gap-2 text-[11px] text-muted-foreground tabular-nums">
                                <span title="Vision Sub-Agent tokens (in / out)">
                                  {formatTokens(tick.usage?.input)} in ·{" "}
                                  {formatTokens(tick.usage?.output)} out
                                  {tick.usage?.duration_s
                                    ? ` · ${tick.usage.duration_s.toFixed(1)}s`
                                    : ""}
                                </span>
                                <span>·</span>
                                <span title={tick.at}>{formatShortTime(tick.at)}</span>
                              </div>
                            </div>

                            <p className="text-xs leading-relaxed text-foreground/95">{summary}</p>

                            {tick.conditions && tick.conditions.length > 0 && (
                              <div className="flex flex-wrap gap-1">
                                {tick.conditions.map((c) => (
                                  <Badge key={c} variant="secondary" className="font-mono text-[10px]">
                                    {c}
                                  </Badge>
                                ))}
                              </div>
                            )}

                            {observations.length > 0 && (
                              <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
                                {observations.slice(0, 4).map((obs, idx) => (
                                  <li key={idx}>{obs}</li>
                                ))}
                              </ul>
                            )}

                            {assets.length > 0 && (
                              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                                {assets
                                  .filter((a) => !a.endsWith("meta.json"))
                                  .map((asset) => (
                                    <button
                                      key={asset}
                                      type="button"
                                      onClick={() =>
                                        setPreviewAsset({
                                          topicId: selectedTopic.id,
                                          bundleId: tick.bundle_id,
                                          asset,
                                        })
                                      }
                                      className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/50 px-2 py-1 font-mono text-[10px] text-muted-foreground hover:border-primary/50 hover:text-foreground cursor-pointer"
                                    >
                                      {asset.endsWith(".mp4") ? (
                                        <Film className="h-3 w-3 text-primary" />
                                      ) : (
                                        <ImageIcon className="h-3 w-3 text-primary" />
                                      )}
                                      {asset}
                                    </button>
                                  ))}
                                <button
                                  type="button"
                                  onClick={() => {
                                    const att = assets[0]
                                      ? `${tick.bundle_id}/${assets[0]}`
                                      : tick.bundle_id;
                                    setPendingAttachments([att]);
                                    setAttachmentLabel(`Bundle ${tick.bundle_id}`);
                                    onChatDraftChange(
                                      selectedTopic.id,
                                      `Regarding tick ${tick.bundle_id} (${tick.status}: "${summary}") — `,
                                    );
                                  }}
                                  className="inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/10 px-2 py-1 text-[10px] font-medium text-primary hover:bg-primary/20 cursor-pointer"
                                >
                                  <MessageSquare className="h-3 w-3" />
                                  Discuss in Chat
                                </button>
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      );
                    })}
                  </div>
                )}
              </TabsContent>

              {/* TAB 2: Mid-Flow Editable Settings & Human-Owned goal.md */}
              <TabsContent value="config">
                <Card>
                  <CardContent className="p-4">
                    <form onSubmit={handleSaveSettings} className="space-y-4">
                      {/* Human-owned goal.md */}
                      <div className="space-y-1.5">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <Label htmlFor="edit-goal" className="flex items-center gap-1.5">
                            <FileText className="h-3.5 w-3.5 text-primary" />
                            goal.md (Monitoring Goal)
                          </Label>
                          <Badge variant="info" className="text-[10px]">
                            Human-owned · read-only to all agents
                          </Badge>
                        </div>
                        <Textarea
                          id="edit-goal"
                          rows={5}
                          value={editGoal}
                          onChange={(e) => {
                            setEditGoal(e.target.value);
                            setDirtyConfig(true);
                          }}
                          className="font-mono text-xs leading-relaxed"
                        />
                      </div>

                      {/* Subscribed Cameras (M:N) */}
                      <div className="space-y-1.5">
                        <Label>Subscribed Cameras (M:N)</Label>
                        <div className="flex flex-wrap gap-1.5">
                          {Array.from(
                            new Set([...cameras.map((c) => c.camera_id), ...editCameras]),
                          ).map((cid) => {
                            const picked = editCameras.includes(cid);
                            return (
                              <button
                                key={cid}
                                type="button"
                                onClick={() => {
                                  setEditCameras((prev) =>
                                    prev.includes(cid)
                                      ? prev.filter((x) => x !== cid)
                                      : [...prev, cid],
                                  );
                                  setDirtyConfig(true);
                                }}
                                className={cn(
                                  "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 font-mono text-xs transition-colors cursor-pointer",
                                  picked
                                    ? "border-primary bg-primary/15 text-primary"
                                    : "border-border bg-muted/40 text-muted-foreground hover:text-foreground",
                                )}
                              >
                                {picked && <Check className="h-3 w-3" />}
                                {cid}
                              </button>
                            );
                          })}
                        </div>
                      </div>

                      {/* Cadence & Window with quick preset pills */}
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <div className="space-y-1.5">
                          <Label htmlFor="edit-cadence">Cadence (seconds)</Label>
                          <Input
                            id="edit-cadence"
                            type="number"
                            min={1}
                            value={editCadence}
                            onChange={(e) => {
                              setEditCadence(Math.max(1, Number(e.target.value) || 60));
                              setDirtyConfig(true);
                            }}
                          />
                          <div className="flex flex-wrap gap-1">
                            {[15, 30, 60, 120, 300].map((s) => (
                              <button
                                key={s}
                                type="button"
                                onClick={() => {
                                  setEditCadence(s);
                                  setDirtyConfig(true);
                                }}
                                className={cn(
                                  "rounded border px-1.5 py-0.5 text-[10px] tabular-nums cursor-pointer",
                                  editCadence === s
                                    ? "border-primary bg-primary/15 text-primary"
                                    : "border-border text-muted-foreground hover:text-foreground",
                                )}
                              >
                                {s}s
                              </button>
                            ))}
                          </div>
                        </div>

                        <div className="space-y-1.5">
                          <Label htmlFor="edit-window">Observation Window (seconds)</Label>
                          <Input
                            id="edit-window"
                            type="number"
                            min={1}
                            value={editWindow}
                            onChange={(e) => {
                              setEditWindow(Math.max(1, Number(e.target.value) || 20));
                              setDirtyConfig(true);
                            }}
                          />
                          <div className="flex flex-wrap gap-1">
                            {[10, 15, 20, 30, 60].map((s) => (
                              <button
                                key={s}
                                type="button"
                                onClick={() => {
                                  setEditWindow(s);
                                  setDirtyConfig(true);
                                }}
                                className={cn(
                                  "rounded border px-1.5 py-0.5 text-[10px] tabular-nums cursor-pointer",
                                  editWindow === s
                                    ? "border-primary bg-primary/15 text-primary"
                                    : "border-border text-muted-foreground hover:text-foreground",
                                )}
                              >
                                {s}s
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>

                      {/* Models */}
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <div className="space-y-1.5">
                          <Label htmlFor="edit-main-model">Main Agent Model</Label>
                          <div className="flex gap-1.5">
                            <Input
                              id="edit-main-model"
                              value={editMainModel}
                              onChange={(e) => {
                                setEditMainModel(e.target.value);
                                setDirtyConfig(true);
                              }}
                              className="font-mono text-xs"
                            />
                            <Input
                              value={editMainEffort}
                              onChange={(e) => {
                                setEditMainEffort(e.target.value);
                                setDirtyConfig(true);
                              }}
                              placeholder="effort"
                              className="w-24 font-mono text-xs"
                            />
                          </div>
                        </div>

                        <div className="space-y-1.5">
                          <Label htmlFor="edit-vision-model">Vision Sub-Agent Model</Label>
                          <div className="flex gap-1.5">
                            <Input
                              id="edit-vision-model"
                              value={editVisionModel}
                              onChange={(e) => {
                                setEditVisionModel(e.target.value);
                                setDirtyConfig(true);
                              }}
                              className="font-mono text-xs"
                            />
                            <Input
                              value={editVisionEffort}
                              onChange={(e) => {
                                setEditVisionEffort(e.target.value);
                                setDirtyConfig(true);
                              }}
                              placeholder="effort"
                              className="w-24 font-mono text-xs"
                            />
                          </div>
                        </div>
                      </div>

                      {/* Toggles: Pause/Resume + Speaker Opt-In */}
                      <div className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-muted/30 p-3 sm:grid-cols-2">
                        <div className="flex items-center justify-between gap-2">
                          <div>
                            <div className="text-xs font-medium">Pause Topic Loop</div>
                            <p className="text-[11px] text-muted-foreground">
                              Temporarily halt ticks while keeping chat active.
                            </p>
                          </div>
                          <Switch
                            checked={editPaused}
                            onCheckedChange={(v) => {
                              setEditPaused(v);
                              setDirtyConfig(true);
                            }}
                          />
                        </div>

                        <div className="flex items-center justify-between gap-2">
                          <div>
                            <div className="flex items-center gap-1.5 text-xs font-medium">
                              <Volume2 className="h-3.5 w-3.5 text-amber-400" />
                              Camera Speaker Opt-In
                            </div>
                            <p className="text-[11px] text-muted-foreground">
                              Allows one-way alert audio if camera also opts in.
                            </p>
                          </div>
                          <Switch
                            checked={editSpeaker}
                            onCheckedChange={(v) => {
                              setEditSpeaker(v);
                              setDirtyConfig(true);
                            }}
                          />
                        </div>
                      </div>

                      <div className="flex items-center justify-between pt-1">
                        <span className="text-[11px] text-muted-foreground">
                          {dirtyConfig ? "Unsaved changes" : "All changes saved"}
                        </span>
                        <Button type="submit" disabled={savingConfig}>
                          <Save className="h-4 w-4" />
                          {savingConfig ? "Saving..." : "Save Goal & Settings"}
                        </Button>
                      </div>
                    </form>
                  </CardContent>
                </Card>
              </TabsContent>

              {/* TAB 3: Rubric, Notes & Deterministic Tools */}
              <TabsContent value="state" className="space-y-3">
                <Card>
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between">
                      <CardTitle className="flex items-center gap-1.5">
                        <Layers className="h-4 w-4 text-primary" />
                        vision_rubric.md
                      </CardTitle>
                      <Badge variant="outline" className="font-mono text-[10px]">
                        sha:{ticksData?.rubric_version ?? selectedTopic.rubric_version}
                      </Badge>
                    </div>
                    <CardDescription>
                      Maintained by the Main Agent for the ephemeral Vision Sub-Agent.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <pre className="max-h-56 overflow-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
                      {ticksData?.vision_rubric || "(empty)"}
                    </pre>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-1.5">
                      <FileText className="h-4 w-4 text-primary" />
                      orchestrator_notes.md
                    </CardTitle>
                    <CardDescription>
                      Pinned durable rules and calibration facts that survive session compaction.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <pre className="max-h-48 overflow-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
                      {ticksData?.orchestrator_notes || "(empty)"}
                    </pre>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-1.5">
                      <Code2 className="h-4 w-4 text-primary" />
                      Helper Tools (tools/)
                    </CardTitle>
                    <CardDescription>
                      Deterministic zero-token preprocessors executed during bundle assembly.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-2.5">
                    {!ticksData || ticksData.tools.length === 0 ? (
                      <p className="text-xs text-muted-foreground">
                        No deterministic helper scripts in <code className="font-mono">tools/</code>{" "}
                        yet. Ask the Main Agent in chat to create one (e.g., ROI crop or motion diff).
                      </p>
                    ) : (
                      ticksData.tools.map((tool) => (
                        <div
                          key={tool.path}
                          className="rounded-lg border border-border bg-muted/30 p-3"
                        >
                          <div className="mb-2 flex items-center justify-between">
                            <span className="font-mono text-xs font-semibold">{tool.path}</span>
                            <Badge variant="outline" className="font-mono text-[10px]">
                              sha:{tool.version}
                            </Badge>
                          </div>
                          <pre className="max-h-48 overflow-auto rounded bg-background/80 p-2.5 font-mono text-[11px] leading-relaxed">
                            {tool.code}
                          </pre>
                        </div>
                      ))
                    )}
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>
          </div>

          {/* RIGHT PANE: Live Main Agent Chat */}
          <Card className="flex h-[680px] flex-col xl:sticky xl:top-4 xl:col-span-5 xl:h-[calc(100vh-8rem)]">
            <CardHeader className="border-b border-border pb-3">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <CardTitle className="flex items-center gap-2">
                    <Bot className="h-4 w-4 text-primary" />
                    Main Agent Chat
                  </CardTitle>
                  <CardDescription className="mt-0.5 truncate font-mono text-[11px]">
                    {selectedTopic.main_model.model}
                    {ticksData?.session?.turns != null
                      ? ` · ${ticksData.session.turns} session turns`
                      : ""}
                  </CardDescription>
                </div>
                {messages.length > 0 && (
                  <Button
                    size="sm"
                    variant="ghost"
                    title="Clear local chat transcript"
                    onClick={() => activeTopicId && onChatHistoryChange(activeTopicId, [])}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </CardHeader>

            {/* Messages scroll area */}
            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {messages.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
                  <div className="rounded-full bg-primary/10 p-3 text-primary">
                    <MessageSquare className="h-5 w-5" />
                  </div>
                  <div className="max-w-xs space-y-1">
                    <p className="text-xs font-medium text-foreground">
                      Tune <span className="font-mono">{selectedTopic.id}</span> live
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      Ask the Main Agent to refine the vision rubric, adjust cadence, write a helper
                      tool, or explain a recent alert.
                    </p>
                  </div>
                  <div className="flex flex-wrap justify-center gap-1.5 pt-1">
                    {[
                      "Summarize what you observed recently",
                      "Tighten the vision rubric to reduce false alarms",
                      "Review current cadence and helper tools",
                    ].map((prompt) => (
                      <button
                        key={prompt}
                        type="button"
                        onClick={() => activeTopicId && onChatDraftChange(activeTopicId, prompt)}
                        className="rounded-full border border-border bg-muted/50 px-2.5 py-1 text-[11px] text-muted-foreground hover:border-primary/50 hover:text-foreground cursor-pointer"
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                messages.map((msg) => (
                  <div
                    key={msg.id}
                    className={cn(
                      "flex flex-col gap-1.5",
                      msg.role === "user" ? "items-end" : "items-start",
                    )}
                  >
                    <div
                      className={cn(
                        "max-w-[90%] rounded-xl px-3.5 py-2.5 text-xs leading-relaxed whitespace-pre-wrap",
                        msg.role === "user" && "bg-primary text-primary-foreground",
                        msg.role === "assistant" &&
                          "border border-border bg-muted/60 text-foreground",
                        msg.role === "error" &&
                          "border border-destructive/40 bg-destructive/10 text-destructive",
                      )}
                    >
                      {msg.role === "error" && (
                        <div className="mb-1 flex items-center gap-1 font-semibold">
                          <ShieldAlert className="h-3.5 w-3.5" />
                          Agent Error
                        </div>
                      )}
                      {msg.content}

                      {msg.attachments && msg.attachments.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {msg.attachments.map((att) => (
                            <span
                              key={att}
                              className="inline-flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5 font-mono text-[10px]"
                            >
                              <Paperclip className="h-2.5 w-2.5" />
                              {att}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Actions taken + token usage metadata */}
                    {msg.actions && msg.actions.length > 0 && (
                      <div className="flex max-w-[90%] flex-wrap gap-1">
                        {msg.actions.map((act, idx) => (
                          <Badge key={idx} variant="info" className="font-mono text-[10px]">
                            <Cpu className="h-2.5 w-2.5" />
                            {act.name}
                          </Badge>
                        ))}
                      </div>
                    )}

                    <div className="flex items-center gap-2 px-1 text-[10px] text-muted-foreground tabular-nums">
                      <span>{formatRelativeTime(msg.timestamp)}</span>
                      {msg.usage && (msg.usage.input > 0 || msg.usage.output > 0) && (
                        <span>
                          · {formatTokens(msg.usage.input)} in / {formatTokens(msg.usage.cached)}{" "}
                          cached / {formatTokens(msg.usage.output)} out
                          {msg.usage.duration_s ? ` (${msg.usage.duration_s.toFixed(1)}s)` : ""}
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}

              {sendingChat && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <RefreshCw className="h-3.5 w-3.5 animate-spin text-primary" />
                  Main Agent is thinking…
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Composer */}
            <div className="border-t border-border p-3 space-y-2">
              {pendingAttachments.length > 0 && (
                <div className="flex items-center justify-between gap-2 rounded-md border border-primary/30 bg-primary/10 px-2.5 py-1.5 text-[11px] text-primary">
                  <div className="flex items-center gap-1.5 truncate">
                    <Paperclip className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate font-medium">
                      {attachmentLabel || pendingAttachments.join(", ")}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setPendingAttachments([]);
                      setAttachmentLabel(null);
                    }}
                    className="rounded p-0.5 hover:bg-primary/20 cursor-pointer"
                    aria-label="Remove attachment"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}

              <div className="flex gap-2">
                <Textarea
                  rows={2}
                  value={draft}
                  onChange={(e) =>
                    activeTopicId && onChatDraftChange(activeTopicId, e.target.value)
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void handleSendChat();
                    }
                  }}
                  placeholder={`Message ${selectedTopic.id}'s Main Agent… (Enter to send, Shift+Enter for newline)`}
                  className="min-h-[60px] resize-none text-xs"
                  disabled={sendingChat}
                />
                <Button
                  type="button"
                  onClick={() => void handleSendChat()}
                  disabled={sendingChat || !draft.trim()}
                  className="h-auto self-stretch px-3.5"
                >
                  <Send className="h-4 w-4" />
                </Button>
              </div>
              <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                <span>Draft auto-saved locally per Topic</span>
                {ticksData?.session?.cumulative_usage && (
                  <span className="tabular-nums">
                    Lifetime: {formatTokens(ticksData.session.cumulative_usage.input)} in
                  </span>
                )}
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* Bundle Asset Preview Modal */}
      <Dialog open={previewAsset !== null} onOpenChange={(o) => !o && setPreviewAsset(null)}>
        <DialogContent className="max-w-3xl">
          {previewAsset && (
            <>
              <DialogHeader>
                <DialogTitle className="font-mono text-sm">
                  {previewAsset.bundleId} / {previewAsset.asset}
                </DialogTitle>
              </DialogHeader>
              <div className="mt-2 overflow-hidden rounded-lg border border-border bg-black">
                {previewAsset.asset.endsWith(".mp4") ? (
                  <video
                    src={api.bundleAssetUrl(
                      previewAsset.topicId,
                      previewAsset.bundleId,
                      previewAsset.asset,
                    )}
                    controls
                    autoPlay
                    playsInline
                    className="max-h-[70vh] w-full"
                  />
                ) : previewAsset.asset.endsWith(".mp3") ? (
                  <div className="p-6">
                    <audio
                      src={api.bundleAssetUrl(
                        previewAsset.topicId,
                        previewAsset.bundleId,
                        previewAsset.asset,
                      )}
                      controls
                      className="w-full"
                    />
                  </div>
                ) : (
                  <img
                    src={api.bundleAssetUrl(
                      previewAsset.topicId,
                      previewAsset.bundleId,
                      previewAsset.asset,
                    )}
                    alt={previewAsset.asset}
                    className="max-h-[70vh] w-full object-contain"
                  />
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      <CreateTopicDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        presets={presets}
        cameras={cameras}
        onCreated={(created) => {
          onTopicsChanged();
          onSelectTopic(created.id);
          onToast(`Created topic "${created.id}"`);
        }}
      />
    </div>
  );
}

function MetricPill({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 text-sm font-semibold tabular-nums">{value}</div>
      <div className="text-[10px] text-muted-foreground tabular-nums">{sub}</div>
    </div>
  );
}
