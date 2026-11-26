import { useCallback, useEffect, useState } from "react";
import {
  Camera,
  Clock,
  Film,
  Grid2x2,
  Grid3x3,
  LayoutGrid,
  Play,
  RefreshCw,
  Square,
} from "lucide-react";
import { api, type CameraInfo, type SegmentInfo } from "@/lib/api";
import type { GridLayout, StreamMode } from "@/lib/storage";
import { cn, formatBytes, formatShortTime } from "@/lib/utils";
import { CameraFeed, healthVariant } from "./CameraFeed";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";

interface LiveWallViewProps {
  cameras: CameraInfo[];
  gridLayout: GridLayout;
  onGridLayoutChange: (layout: GridLayout) => void;
  streamMode: StreamMode;
  onStreamModeChange: (mode: StreamMode) => void;
  selectedCameraId: string | null;
  onSelectCamera: (id: string) => void;
  onRefresh: () => void;
}

export function LiveWallView({
  cameras,
  gridLayout,
  onGridLayoutChange,
  streamMode,
  onStreamModeChange,
  selectedCameraId,
  onSelectCamera,
  onRefresh,
}: LiveWallViewProps) {
  const [expandedCamera, setExpandedCamera] = useState<CameraInfo | null>(null);
  const [segments, setSegments] = useState<SegmentInfo[]>([]);
  const [loadingSegs, setLoadingSegs] = useState(false);
  const [activePlayerSrc, setActivePlayerSrc] = useState<string | null>(null);
  const [activePlayerLabel, setActivePlayerLabel] = useState<string>("");

  const activeCameraId =
    selectedCameraId && cameras.some((c) => c.camera_id === selectedCameraId)
      ? selectedCameraId
      : cameras[0]?.camera_id ?? null;

  const loadSegments = useCallback(async (cameraId: string) => {
    setLoadingSegs(true);
    try {
      const res = await api.cameraSegments(cameraId);
      setSegments(res.segments);
    } catch {
      setSegments([]);
    } finally {
      setLoadingSegs(false);
    }
  }, []);

  useEffect(() => {
    if (!activeCameraId) {
      setSegments([]);
      return;
    }
    void loadSegments(activeCameraId);
    const id = window.setInterval(() => void loadSegments(activeCameraId), 10_000);
    return () => window.clearInterval(id);
  }, [activeCameraId, loadSegments]);

  const okCount = cameras.filter((c) => c.health.status === "ok").length;
  const degradedCount = cameras.filter((c) => c.health.status === "degraded").length;
  const offlineCount = cameras.filter((c) => c.health.status === "offline").length;

  const gridClass =
    gridLayout === "1"
      ? "grid-cols-1"
      : gridLayout === "2"
        ? "grid-cols-1 md:grid-cols-2"
        : gridLayout === "3"
          ? "grid-cols-1 md:grid-cols-2 xl:grid-cols-3"
          : cameras.length <= 1
            ? "grid-cols-1 max-w-4xl"
            : cameras.length <= 4
              ? "grid-cols-1 md:grid-cols-2"
              : "grid-cols-1 md:grid-cols-2 xl:grid-cols-3";

  return (
    <div className="space-y-5">
      {/* Header toolbar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Live Wall & Timeline</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{cameras.length} camera{cameras.length === 1 ? "" : "s"}</span>
            {okCount > 0 && <Badge variant="success">{okCount} ok</Badge>}
            {degradedCount > 0 && <Badge variant="warning">{degradedCount} degraded</Badge>}
            {offlineCount > 0 && <Badge variant="danger">{offlineCount} offline</Badge>}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Stream mode toggle */}
          <div className="inline-flex rounded-lg border border-border bg-muted p-0.5 text-xs">
            <button
              type="button"
              onClick={() => onStreamModeChange("mjpeg")}
              className={cn(
                "rounded-md px-2.5 py-1 font-medium transition-colors cursor-pointer",
                streamMode === "mjpeg"
                  ? "bg-card text-foreground shadow-2xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              Live Stream
            </button>
            <button
              type="button"
              onClick={() => onStreamModeChange("snapshot")}
              className={cn(
                "rounded-md px-2.5 py-1 font-medium transition-colors cursor-pointer",
                streamMode === "snapshot"
                  ? "bg-card text-foreground shadow-2xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              Snapshots
            </button>
          </div>

          {/* Grid layout selector */}
          <div className="inline-flex rounded-lg border border-border bg-muted p-0.5">
            {(
              [
                { id: "auto", label: "Auto", Icon: LayoutGrid },
                { id: "1", label: "1x1", Icon: Square },
                { id: "2", label: "2x2", Icon: Grid2x2 },
                { id: "3", label: "3x3", Icon: Grid3x3 },
              ] as const
            ).map(({ id, label, Icon }) => (
              <button
                key={id}
                type="button"
                title={`Grid: ${label}`}
                onClick={() => onGridLayoutChange(id)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors cursor-pointer",
                  gridLayout === id
                    ? "bg-card text-foreground shadow-2xs"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">{label}</span>
              </button>
            ))}
          </div>

          <Button size="sm" variant="outline" onClick={onRefresh}>
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Camera Grid */}
      {cameras.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-14 text-center">
            <Camera className="h-9 w-9 text-muted-foreground/60" />
            <p className="text-sm font-medium">No cameras configured in Stream Hub</p>
            <p className="max-w-md text-xs text-muted-foreground">
              Add a camera (RTSP, ONVIF, USB webcam, RTMP push, or local file replay) in{" "}
              <code className="rounded bg-muted px-1 py-0.5">vision-sentinel.toml</code> to see
              live feeds and rolling recordings here.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className={cn("grid gap-4", gridClass)}>
          {cameras.map((cam) => (
            <CameraFeed
              key={cam.camera_id}
              camera={cam}
              streamMode={streamMode}
              selected={cam.camera_id === activeCameraId}
              onSelect={() => {
                onSelectCamera(cam.camera_id);
                setActivePlayerSrc(null);
              }}
              onExpand={() => setExpandedCamera(cam)}
            />
          ))}
        </div>
      )}

      {/* Rolling Buffer Timeline & Clip Scrubber */}
      {activeCameraId && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Film className="h-4 w-4 text-primary" />
                  Rolling Buffer Timeline
                  <Badge variant="secondary" className="font-mono">
                    {activeCameraId}
                  </Badge>
                </CardTitle>
                <CardDescription className="mt-1">
                  Scrub recent fMP4 segments or extract a rolling clip directly from Stream Hub.
                </CardDescription>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {[15, 30, 60].map((sec) => (
                  <Button
                    key={sec}
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setActivePlayerSrc(api.cameraClipUrl(activeCameraId, sec, Date.now()));
                      setActivePlayerLabel(`${activeCameraId} · Last ${sec}s clip`);
                    }}
                  >
                    <Play className="h-3 w-3" />
                    Last {sec}s
                  </Button>
                ))}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void loadSegments(activeCameraId)}
                  disabled={loadingSegs}
                >
                  <RefreshCw className={cn("h-3.5 w-3.5", loadingSegs && "animate-spin")} />
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Camera quick selector pills */}
            {cameras.length > 1 && (
              <div className="flex flex-wrap gap-1.5">
                {cameras.map((cam) => (
                  <button
                    key={cam.camera_id}
                    type="button"
                    onClick={() => {
                      onSelectCamera(cam.camera_id);
                      setActivePlayerSrc(null);
                    }}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer",
                      cam.camera_id === activeCameraId
                        ? "border-primary bg-primary/15 text-primary"
                        : "border-border bg-muted/40 text-muted-foreground hover:text-foreground",
                    )}
                  >
                    <span className="font-mono">{cam.camera_id}</span>
                    <Badge variant={healthVariant(cam.health.status)} className="px-1.5 py-0 text-[10px]">
                      {cam.health.status}
                    </Badge>
                  </button>
                ))}
              </div>
            )}

            {/* Segment scrubber strip */}
            {segments.length === 0 ? (
              <div className="flex items-center gap-2 rounded-lg border border-dashed border-border px-4 py-6 text-xs text-muted-foreground">
                <Clock className="h-4 w-4" />
                No rolling segments recorded yet for <span className="font-mono">{activeCameraId}</span>.
              </div>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-[11px] text-muted-foreground tabular-nums">
                  <span>Oldest: {formatShortTime(segments[0]?.start)}</span>
                  <span>{segments.length} rolling segments (click any block to play)</span>
                  <span>Latest: {formatShortTime(segments[segments.length - 1]?.start)}</span>
                </div>
                <div className="flex gap-1 overflow-x-auto rounded-lg border border-border bg-muted/40 p-2">
                  {segments.slice(-48).map((seg) => {
                    const url = api.cameraSegmentUrl(activeCameraId, seg.name);
                    const isPlaying = activePlayerSrc === url;
                    return (
                      <button
                        key={seg.name}
                        type="button"
                        title={`${seg.name} (${formatShortTime(seg.start)} · ${seg.duration_sec ?? "?"}s · ${formatBytes(seg.size_bytes)})`}
                        onClick={() => {
                          setActivePlayerSrc(url);
                          setActivePlayerLabel(
                            `${activeCameraId} · ${formatShortTime(seg.start)} (${seg.duration_sec ?? "?"}s)`,
                          );
                        }}
                        className={cn(
                          "group flex h-10 min-w-[20px] flex-1 flex-col items-center justify-end rounded-sm border transition-all cursor-pointer",
                          isPlaying
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-primary/25 bg-primary/20 hover:bg-primary/40",
                        )}
                      >
                        <span className="sr-only">{seg.name}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Embedded HTML5 video player when a segment or clip is selected */}
            {activePlayerSrc && (
              <div className="space-y-2 rounded-lg border border-border bg-black/95 p-3">
                <div className="flex items-center justify-between text-xs text-slate-300">
                  <span className="font-mono">{activePlayerLabel}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 text-xs text-slate-300 hover:bg-white/10 hover:text-white"
                    onClick={() => setActivePlayerSrc(null)}
                  >
                    Close Player
                  </Button>
                </div>
                <video
                  key={activePlayerSrc}
                  src={activePlayerSrc}
                  controls
                  autoPlay
                  playsInline
                  className="max-h-[420px] w-full rounded-md bg-black"
                />
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Fullscreen Camera Modal */}
      <Dialog open={expandedCamera !== null} onOpenChange={(o) => !o && setExpandedCamera(null)}>
        <DialogContent className="max-w-4xl">
          {expandedCamera && (
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2 font-mono">
                  {expandedCamera.camera_id}
                  <Badge variant={healthVariant(expandedCamera.health.status)}>
                    {expandedCamera.health.status}
                  </Badge>
                </DialogTitle>
              </DialogHeader>
              <div className="mt-2">
                <CameraFeed camera={expandedCamera} streamMode={streamMode} />
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
