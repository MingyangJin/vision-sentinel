import { useEffect, useState } from "react";
import { DoorOpen, LayoutGrid, Package, PawPrint, Plus, Sparkles, Volume2 } from "lucide-react";
import { api, type CameraInfo, type TopicPreset, type TopicSummary } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Input, Label, Textarea } from "./ui/input";
import { Switch } from "./ui/switch";

interface CreateTopicDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  presets: TopicPreset[];
  cameras: CameraInfo[];
  onCreated: (topic: TopicSummary) => void;
}

function PresetIcon({ id }: { id: string }) {
  if (id.includes("package")) return <Package className="h-4 w-4" />;
  if (id.includes("door")) return <DoorOpen className="h-4 w-4" />;
  if (id.includes("pet")) return <PawPrint className="h-4 w-4" />;
  if (id.includes("multi")) return <LayoutGrid className="h-4 w-4" />;
  return <Sparkles className="h-4 w-4" />;
}

export function CreateTopicDialog({
  open,
  onOpenChange,
  presets,
  cameras,
  onCreated,
}: CreateTopicDialogProps) {
  const [selectedPreset, setSelectedPreset] = useState<string>("package-watch");
  const [topicId, setTopicId] = useState("");
  const [cameraIds, setCameraIds] = useState<string[]>([]);
  const [goal, setGoal] = useState("");
  const [cadenceSec, setCadenceSec] = useState(60);
  const [windowSec, setWindowSec] = useState(20);
  const [mainModel, setMainModel] = useState("gemini-4-neon-medium");
  const [visionModel, setVisionModel] = useState("gemini-4-hydrogen-low");
  const [speakerEnabled, setSpeakerEnabled] = useState(false);
  const [paused, setPaused] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyPreset = (presetId: string) => {
    setSelectedPreset(presetId);
    const p = presets.find((x) => x.id === presetId);
    if (!p) return;
    setGoal(p.goal);
    setCadenceSec(p.cadence_sec);
    setWindowSec(p.window_sec);
    setSpeakerEnabled(Boolean(p.speaker_enabled));
    if (!topicId || presets.some((x) => x.id === topicId)) {
      setTopicId(presetId === "custom" ? "" : presetId);
    }
    if (cameras.length > 0) {
      const want = p.recommended_cameras ?? 1;
      setCameraIds(cameras.slice(0, Math.min(want, cameras.length)).map((c) => c.camera_id));
    }
  };

  useEffect(() => {
    if (open && presets.length > 0) {
      applyPreset(presets[0].id);
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const toggleCamera = (cid: string) => {
    setCameraIds((prev) =>
      prev.includes(cid) ? prev.filter((x) => x !== cid) : [...prev, cid],
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const cleanId = topicId.trim();
    if (!cleanId) {
      setError("Please enter a Topic ID (e.g., front-porch-packages).");
      return;
    }
    const cams = cameraIds.length > 0 ? cameraIds : cameras.slice(0, 1).map((c) => c.camera_id);
    if (cams.length === 0) {
      setError("Select or enter at least one camera_id.");
      return;
    }
    setSubmitting(true);
    try {
      const created = await api.createTopic({
        id: cleanId,
        camera_ids: cams,
        preset: selectedPreset,
        goal,
        cadence_sec: cadenceSec,
        window_sec: windowSec,
        main_model: { model: mainModel, effort: "medium" },
        vision_model: { model: visionModel, effort: "low" },
        speaker_enabled: speakerEnabled,
        paused,
      });
      onCreated(created);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create topic");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create Monitoring Topic</DialogTitle>
          <DialogDescription>
            Pick a quick-start preset or configure a custom Topic. Every setting (including{" "}
            <code className="font-mono">goal.md</code>, cameras, cadence, and models) stays editable
            mid-flow after creation.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="mt-3 space-y-4">
          {/* Quick-Select Presets */}
          <div className="space-y-2">
            <Label>Quick-Select Preset</Label>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {presets.map((p) => {
                const active = selectedPreset === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => applyPreset(p.id)}
                    className={cn(
                      "flex flex-col items-start gap-1 rounded-lg border p-2.5 text-left transition-all cursor-pointer",
                      active
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-muted/30 text-muted-foreground hover:border-primary/40 hover:text-foreground",
                    )}
                  >
                    <div className="flex w-full items-center justify-between gap-1.5">
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-foreground">
                        <PresetIcon id={p.id} />
                        {p.name}
                      </span>
                      {(p.recommended_cameras ?? 1) > 1 && (
                        <Badge variant="info" className="text-[10px]">
                          M:N
                        </Badge>
                      )}
                    </div>
                    <p className="line-clamp-2 text-[11px] leading-snug text-muted-foreground">
                      {p.description}
                    </p>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="topic-id">Topic ID</Label>
              <Input
                id="topic-id"
                value={topicId}
                onChange={(e) => setTopicId(e.target.value)}
                placeholder="e.g. front-porch-packages"
                className="font-mono text-xs"
                required
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1.5">
                <Label htmlFor="topic-cadence">Cadence (sec)</Label>
                <Input
                  id="topic-cadence"
                  type="number"
                  min={5}
                  value={cadenceSec}
                  onChange={(e) => setCadenceSec(Math.max(5, Number(e.target.value) || 60))}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="topic-window">Window (sec)</Label>
                <Input
                  id="topic-window"
                  type="number"
                  min={2}
                  value={windowSec}
                  onChange={(e) => setWindowSec(Math.max(2, Number(e.target.value) || 20))}
                />
              </div>
            </div>
          </div>

          {/* M:N Camera Subscriptions */}
          <div className="space-y-1.5">
            <Label>Subscribed Cameras (1 or more · M:N)</Label>
            {cameras.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {cameras.map((cam) => {
                  const picked = cameraIds.includes(cam.camera_id);
                  return (
                    <button
                      key={cam.camera_id}
                      type="button"
                      onClick={() => toggleCamera(cam.camera_id)}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 font-mono text-xs transition-colors cursor-pointer",
                        picked
                          ? "border-primary bg-primary/15 text-primary"
                          : "border-border bg-muted/40 text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <span>{cam.camera_id}</span>
                      <span className="text-[10px] opacity-75">({cam.health.status})</span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <Input
                placeholder="Comma-separated camera IDs (e.g. demo)"
                value={cameraIds.join(",")}
                onChange={(e) =>
                  setCameraIds(
                    e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  )
                }
                className="font-mono text-xs"
              />
            )}
          </div>

          {/* Human-owned goal.md */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor="topic-goal">Monitoring Goal (goal.md)</Label>
              <Badge variant="outline" className="text-[10px]">
                Human-owned · read-only to agents
              </Badge>
            </div>
            <Textarea
              id="topic-goal"
              rows={4}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Describe what to watch for and when to alert you..."
            />
          </div>

          {/* Models */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="main-model">Main Agent Model</Label>
              <Input
                id="main-model"
                value={mainModel}
                onChange={(e) => setMainModel(e.target.value)}
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="vision-model">Vision Sub-Agent Model</Label>
              <Input
                id="vision-model"
                value={visionModel}
                onChange={(e) => setVisionModel(e.target.value)}
                className="font-mono text-xs"
              />
            </div>
          </div>

          {/* Switches */}
          <div className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-muted/30 p-3 sm:grid-cols-2">
            <div className="flex items-center justify-between gap-2">
              <div className="space-y-0.5">
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  <Volume2 className="h-3.5 w-3.5 text-amber-400" />
                  Speaker Alert Opt-In
                </div>
                <p className="text-[11px] text-muted-foreground">
                  Off by default. Requires camera opt-in too.
                </p>
              </div>
              <Switch checked={speakerEnabled} onCheckedChange={setSpeakerEnabled} />
            </div>

            <div className="flex items-center justify-between gap-2">
              <div className="space-y-0.5">
                <div className="text-xs font-medium">Start Paused</div>
                <p className="text-[11px] text-muted-foreground">
                  Configure & chat before ticks begin.
                </p>
              </div>
              <Switch checked={paused} onCheckedChange={setPaused} />
            </div>
          </div>

          {error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              <Plus className="h-4 w-4" />
              {submitting ? "Creating..." : "Create Topic"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
