import { useEffect, useRef, useState } from "react";
import { Maximize2, Radio, VideoOff, Volume2 } from "lucide-react";
import { api, type CameraInfo, type CameraStatus } from "@/lib/api";
import type { StreamMode } from "@/lib/storage";
import { cn, formatRelativeTime } from "@/lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

export function healthVariant(status: CameraStatus): "success" | "warning" | "danger" {
  if (status === "ok") return "success";
  if (status === "degraded") return "warning";
  return "danger";
}

/** Extract `avc1.PPCCLL` from the first `avcC` box in an H.264 MP4 init segment. */
function detectAvcCodec(bytes: Uint8Array): string | null {
  for (let i = 0; i + 8 < bytes.length; i++) {
    if (
      bytes[i] === 0x61 && // 'a'
      bytes[i + 1] === 0x76 && // 'v'
      bytes[i + 2] === 0x63 && // 'c'
      bytes[i + 3] === 0x43 // 'C'
    ) {
      // avcC payload: version(1), AVCProfileIndication(1), profile_compatibility(1), AVCLevelIndication(1)
      const p = bytes[i + 5].toString(16).padStart(2, "0");
      const c = bytes[i + 6].toString(16).padStart(2, "0");
      const l = bytes[i + 7].toString(16).padStart(2, "0");
      return `video/mp4; codecs="avc1.${p}${c}${l}"`;
    }
  }
  return null;
}

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(new ArrayBuffer(a.length + b.length));
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

interface LiveMp4VideoProps {
  cameraId: string;
  onFallback: () => void;
}

function LiveMp4Video({ cameraId, onFallback }: LiveMp4VideoProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const onFallbackRef = useRef(onFallback);
  onFallbackRef.current = onFallback;
  const [posterUrl] = useState(() => api.cameraSnapshotUrl(cameraId, Date.now()));

  useEffect(() => {
    const video = videoRef.current;
    const MS: typeof MediaSource | undefined =
      typeof window !== "undefined"
        ? (window.MediaSource ?? (window as unknown as { ManagedMediaSource?: typeof MediaSource }).ManagedMediaSource)
        : undefined;
    if (!video || !MS) {
      onFallbackRef.current();
      return;
    }

    const ac = new AbortController();
    let closed = false;
    const ms = new MS();
    const objectUrl = URL.createObjectURL(ms);
    video.src = objectUrl;

    let sb: SourceBuffer | null = null;
    const queue: Uint8Array[] = [];
    let appending = false;

    const flush = () => {
      if (closed || !sb || appending || sb.updating) return;
      // Keep browser buffer bounded (~15 s behind playback) so RAM stays flat over hours.
      try {
        if (sb.buffered.length > 0 && video.currentTime > 20) {
          const start = sb.buffered.start(0);
          const trimTo = video.currentTime - 15;
          if (trimTo - start > 5) {
            appending = true;
            sb.remove(start, trimTo);
            return;
          }
        }
      } catch {
        // ignore transient remove errors
      }
      const next = queue.shift();
      if (!next) return;
      try {
        appending = true;
        sb.appendBuffer(next.buffer.slice(next.byteOffset, next.byteOffset + next.byteLength) as ArrayBuffer);
      } catch {
        appending = false;
        // QuotaExceededError or decode hiccup -> clear queue & fall back if fatal
        queue.length = 0;
      }
    };

    const keepLiveEdge = () => {
      if (!video || !sb || sb.buffered.length === 0) return;
      const end = sb.buffered.end(sb.buffered.length - 1);
      if (video.paused) {
        void video.play().catch(() => {});
      }
      if (end - video.currentTime > 4.5) {
        video.currentTime = Math.max(0, end - 0.35);
      }
    };

    const run = async () => {
      await new Promise<void>((resolve) => {
        if (ms.readyState === "open") {
          resolve();
        } else {
          ms.addEventListener("sourceopen", () => resolve(), { once: true });
        }
      });
      if (closed) return;

      const res = await fetch(api.cameraLiveMp4Url(cameraId), {
        signal: ac.signal,
        cache: "no-store",
      });
      if (!res.ok || !res.body) {
        if (!closed) onFallbackRef.current();
        return;
      }
      const reader = res.body.getReader();
      let initBuf: Uint8Array<ArrayBuffer> = new Uint8Array(new ArrayBuffer(0));

      while (!closed) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!value || value.length === 0) continue;

        if (!sb) {
          initBuf = concatBytes(initBuf, value);
          const detected = detectAvcCodec(initBuf);
          const mime =
            detected && MS.isTypeSupported(detected)
              ? detected
              : MS.isTypeSupported('video/mp4; codecs="avc1.640028"')
                ? 'video/mp4; codecs="avc1.640028"'
                : 'video/mp4; codecs="avc1.42E01E"';
          if (!detected && initBuf.length < 65536) {
            continue;
          }
          if (!MS.isTypeSupported(mime) || ms.readyState !== "open") {
            if (!closed) onFallbackRef.current();
            return;
          }
          sb = ms.addSourceBuffer(mime);
          try {
            sb.mode = "sequence";
          } catch {
            // some browsers default to segments; continue cleanly
          }
          sb.addEventListener("updateend", () => {
            appending = false;
            keepLiveEdge();
            flush();
          });
          queue.push(initBuf);
          initBuf = new Uint8Array(new ArrayBuffer(0));
          flush();
        } else {
          // Drop stale queued chunks if browser tab was backgrounded
          if (queue.length > 24) {
            queue.splice(0, queue.length - 4);
          }
          queue.push(value);
          flush();
        }
      }
    };

    void run().catch(() => {
      if (!closed) onFallbackRef.current();
    });

    return () => {
      closed = true;
      ac.abort();
      try {
        if (ms.readyState === "open") {
          ms.endOfStream();
        }
      } catch {
        // ignore teardown errors
      }
      video.removeAttribute("src");
      video.load();
      URL.revokeObjectURL(objectUrl);
    };
  }, [cameraId]);

  return (
    <video
      ref={videoRef}
      poster={posterUrl}
      autoPlay
      muted
      playsInline
      className="h-full w-full object-cover"
    />
  );
}

interface CameraFeedProps {
  camera: CameraInfo;
  streamMode: StreamMode;
  compact?: boolean;
  selected?: boolean;
  onSelect?: () => void;
  onExpand?: () => void;
}

export function CameraFeed({
  camera,
  streamMode,
  compact = false,
  selected = false,
  onSelect,
  onExpand,
}: CameraFeedProps) {
  const [snapTick, setSnapTick] = useState(() => Date.now());
  const [mseFailed, setMseFailed] = useState(false);
  const [streamFailed, setStreamFailed] = useState(false);
  const [snapFailed, setSnapFailed] = useState(false);

  const status = camera.health.status;
  const isOffline = status === "offline" && !camera.has_live_stream;
  const useLiveMp4 = !compact && streamMode === "mjpeg" && !mseFailed && !isOffline;
  const useSnapshot = streamMode === "snapshot" || streamFailed;

  useEffect(() => {
    setMseFailed(false);
    setStreamFailed(false);
    setSnapFailed(false);
  }, [camera.camera_id, streamMode]);

  useEffect(() => {
    if (!useSnapshot || isOffline || useLiveMp4) return;
    const id = window.setInterval(() => {
      setSnapTick(Date.now());
      setSnapFailed(false);
    }, 2500);
    return () => window.clearInterval(id);
  }, [useSnapshot, isOffline, useLiveMp4]);

  const imgSrc = useSnapshot
    ? api.cameraSnapshotUrl(camera.camera_id, snapTick)
    : api.cameraStreamUrl(camera.camera_id, compact ? 1.5 : 5.0);

  return (
    <div
      onClick={onSelect}
      className={cn(
        "group relative flex flex-col overflow-hidden rounded-xl border bg-card transition-all",
        selected
          ? "border-primary ring-2 ring-primary/30"
          : "border-border hover:border-primary/40",
        onSelect && "cursor-pointer",
      )}
    >
      {/* Video frame area */}
      <div className={cn("relative aspect-video w-full overflow-hidden bg-black/90")}>
        {isOffline || (!useLiveMp4 && useSnapshot && snapFailed) ? (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-4 text-center text-slate-400">
            <VideoOff className="h-7 w-7 opacity-60" />
            <div className="text-xs font-medium text-slate-300">
              {status === "offline" ? "Feed Offline" : "Waiting for frame…"}
            </div>
            {camera.health.detail && (
              <div className="max-w-[260px] truncate text-[11px] text-slate-500">
                {camera.health.detail}
              </div>
            )}
          </div>
        ) : useLiveMp4 ? (
          <LiveMp4Video
            key={`${camera.camera_id}-fmp4`}
            cameraId={camera.camera_id}
            onFallback={() => setMseFailed(true)}
          />
        ) : (
          <img
            key={`${camera.camera_id}-${useSnapshot ? "snap" : "mjpeg"}`}
            src={imgSrc}
            alt={`Camera ${camera.camera_id}`}
            className="h-full w-full object-cover"
            onError={() => {
              if (!useSnapshot) {
                setStreamFailed(true);
              } else {
                setSnapFailed(true);
              }
            }}
          />
        )}

        {/* Top gradient HUD */}
        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between gap-2 bg-gradient-to-b from-black/75 via-black/30 to-transparent px-3 py-2">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className={cn(
                "h-2 w-2 shrink-0 rounded-full",
                status === "ok" && "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.85)]",
                status === "degraded" && "bg-amber-400",
                status === "offline" && "bg-rose-500",
              )}
            />
            <span className="truncate font-mono text-xs font-semibold text-white">
              {camera.camera_id}
            </span>
            {!compact && (
              <span className="hidden rounded bg-white/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-white/80 sm:inline">
                {camera.adapter}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            {camera.speaker_enabled && (
              <span
                title={`Speaker opt-in (cap ${Math.round(camera.volume_cap * 100)}%)`}
                className="inline-flex items-center rounded-full bg-amber-500/25 p-1 text-amber-300"
              >
                <Volume2 className="h-3 w-3" />
              </span>
            )}
            <Badge variant={healthVariant(status)} className="backdrop-blur-xs">
              {status}
            </Badge>
          </div>
        </div>

        {/* Hover expand button */}
        {onExpand && (
          <div className="absolute bottom-2 right-2 opacity-0 transition-opacity group-hover:opacity-100">
            <Button
              type="button"
              size="icon"
              variant="secondary"
              className="h-7 w-7 bg-black/70 text-white hover:bg-black/90"
              onClick={(e) => {
                e.stopPropagation();
                onExpand();
              }}
              title="Expand camera"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}
      </div>

      {/* Footer metadata bar */}
      {!compact && (
        <div className="flex items-center justify-between gap-2 px-3 py-2 text-[11px] text-muted-foreground">
          <div className="flex items-center gap-2 truncate tabular-nums">
            <Radio className="h-3 w-3 shrink-0 text-primary" />
            <span>
              {useLiveMp4
                ? "Live H.264 (Native FPS)"
                : useSnapshot
                  ? "Snapshot"
                  : "Live MJPEG"}
            </span>
            <span>·</span>
            <span>{camera.segment_count} segs</span>
          </div>
          <span className="shrink-0 tabular-nums">
            {camera.health.last_segment_at
              ? `Updated ${formatRelativeTime(camera.health.last_segment_at)}`
              : "No segments"}
          </span>
        </div>
      )}
    </div>
  );
}
