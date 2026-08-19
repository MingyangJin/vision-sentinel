# Reolink E1 Pro — measured behaviour

Everything here was measured against the actual camera, not read off a spec
sheet. Recorded 2026-08-18. Firmware build `2412122130`, `cfgVer v3.1.0.0`.

The measurement harness that produced these numbers was deliberately not kept
in the repo — it was scaffolding, and the findings are the deliverable.

## Streams

| | Resolution | fps | Bitrate |
|---|---|---|---|
| substream | 896×512 (**fixed**) | 10 | 181 kbps observed, 1024 kbps cap |
| main | 2880×1616 | 20 | 3396 kbps |

Substream **resolution cannot be changed** on this model. Only bitrate
(128/256/384/512/768/1024/1228 kbps) and frame rate (4/7/10/15) are adjustable.

The main stream offers three presets, each with its own ceiling:

| Resolution | Bitrate options (kbps) | Max fps |
|---|---|---|
| 2880×1616 | 2048 / 3072 / 4096 / 5120 | 20 |
| 2560×1440 | 2048 / 3072 / 4096 | 25 |
| 2304×1296 | 1024 / 1536 / 2048 / 2560 / 3072 / 4096 | 20 |

GOP is capped at 2s (main) and 4s (sub). That sets clip-extraction
granularity: without re-encoding, clips can only be cut on keyframes.

Both streams are H.264. The substream carries **audio** as well as video.

Bitrate settings are a ceiling, not a target — a static daylight scene sat at
181 kbps against a 1024 kbps cap. Expect the gap to close at night, since IR
introduces sensor grain and grain does not compress.

## Cost to consume

Continuously decoding the substream to raw RGB costs **~3% of one core** on an
M2 Pro (0.66s CPU per 20s of video), 74 MB resident. Hardware acceleration is
not needed for a single camera at this rate.

A 12-hour Python decode loop held flat at **17.7 MB RSS** across 318,912
frames — below a fresh interpreter's own peak. No leak.

## Reliability

A 12-hour continuous run:

```
10:47:28  connect (4.0s)
          ... 8h 52m, 318,912 frames, 10.00 fps every minute ...
19:38:22  last healthy minute      fps 10.00
19:39:28  30.4s gap, stream ends
19:39:28  193 reconnect attempts, all failing, over the next 3h 08m
```

The camera itself performed **excellently**: 530 of 531 minutes at full frame
rate, 0.09% frame loss, exactly one gap over 1 second in nearly nine hours,
zero reconnects. Wi-Fi was not a problem.

### The 19:39 failure was host-side, not the camera

Initially recorded here as a camera failure. That was wrong, and the
correction is more useful than the original claim.

At the time of the failure and for hours afterward the camera was serving RTSP
normally — an authenticated `DESCRIBE` returned `200 OK` with a full SDP, and
the vendor app streamed fine. The failure was that **ffmpeg on the monitoring
host could no longer reach the camera**:

| Tool -> 192.168.4.44 | Result |
|---|---|
| python / curl / nc | connect fine |
| vendor app | streams fine |
| manual RTSP `DESCRIBE` | `200 OK` + SDP |
| **ffmpeg / ffprobe** | **`No route to host` in 0.02s** |

A copy of ffprobe at a different path failed identically, ruling out per-binary
permission. ffmpeg reached other LAN hosts normally. The block was specific to
this one address, from this one program, on this one host.

Two candidates, unresolved: a Tailscale network extension intercepting flows,
or leftover pf state from an egress-capture experiment that had installed a NAT
rule for exactly this address and disabled pf's rules without disabling pf.

The single long session survived because its TCP connection was already
established; once it ended, no new connection could be made.

### Why this matters more than a camera fault would have

Every liveness check reported healthy throughout:

| Check | Result |
|---|---|
| ICMP ping | replies |
| TCP 554 open | 36/36 samples |
| RTSP `OPTIONS` | `RTSP/1.0 200 OK` |
| HTTP API login | succeeds |
| Camera's own status | `"online": 1` |
| **Frames delivered** | **zero** |

The camera was healthy, the network was healthy, every probe said healthy, and
the pipeline received nothing for three hours.

A watchdog must therefore detect **its own inability to obtain frames**, not
the camera's health. Those are different questions, and only the frame count
answers the one that matters. It also argues for the watchdog running on
separate hardware, since a host-side fault like this one is invisible to
anything sharing the host.

### Consequences for the watchdog

- **Alarm on sustained frame-rate deficit.** Ping, port checks, RTSP `OPTIONS`
  and the camera's own status API all reported healthy while nothing arrived.
- **Reconnect takes ~4.0s**, very consistent. A staleness threshold below ~10s
  would fire on every routine reconnect.
- **Do not alarm on inter-arrival gaps.** ffmpeg delivers frames in bursts, so
  a ~0.32s pause followed by frames landing together is normal. A 12h run
  logged 7,991 such bursts while receiving 318,933 frames against 319,206
  expected - alarming on these would produce ~660 false alarms per hour.
- **Recovery must escalate beyond reconnecting.** A retry loop alone ran 193
  times over three hours against a fault no retry could fix.

## Network behaviour

Measured by routing the camera's WAN traffic through a host running tcpdump.

**At idle** the camera is well behaved. It contacts exactly one host — a
185-byte UDP heartbeat every 10.0s to a Linode/Akamai address, roughly
1.6 MB/day. No analytics fan-out, no secondary endpoints, and no DNS queries
at all (the address is cached or hardcoded).

**Remote viewing is the entire exposure.** Viewing from a phone on cellular:

```
camera -> phone's carrier IP    6 packets, 0.7 KB, then abandoned
camera -> Linode/Akamai relay   2,360 packets, 200 KB, peak 334 kbps
```

Direct peer-to-peer fails against carrier-grade NAT and the stream falls back
to a relay. **One minute of remote viewing sent more data than 110 minutes of
idle operation.** Any time this camera is viewed from outside the house, live
video transits third-party infrastructure.

Relay payloads are near-random (median per-packet entropy 6.80 against a 6.96
ceiling for that packet size). That is consistent with encryption — but also
with raw compressed video, which is itself high-entropy. **This measurement
cannot establish whether the relay operator can read the stream.**

### Practical posture

Vision Sentinel needs only LAN RTSP. Disabling UID/P2P and blocking WAN
removes the relay exposure entirely, at the cost of the vendor app's remote
view — a feature this project replaces anyway. The camera streamed for 5h21m
with no internet access at all, confirming nothing in the local path needs it.

## Addressing

**The camera changes IP.** A DHCP renegotiation moved it from `192.168.4.44` to
`192.168.4.20` mid-project, which would silently break any running instance.

Its **MAC is stable** — `0e:c7:1d:be:6b:b8`, identical before and after the
move, confirmed from both the ARP cache and the camera's own API. Note this is
a locally-administered address, not Reolink's registered OUI, so the camera
cannot be identified by vendor prefix.

Two layers, both worth having:

- **Reserve the address** on the router. The stable MAC means a reservation
  will hold.
- **Configure by MAC, not IP.** `sources.locate.find()` resolves a MAC to its
  current address, scanning the subnet by RTSP port if the ARP cache misses.
  A reservation is a promise made by a router we do not control; this is the
  layer that survives it not being kept.

The camera does not announce itself over mDNS, so hostname discovery is not an
option.

## Pan / tilt

Full PTZ over the HTTP API: `ptzCtrl`, `ptzDirection`, 64 preset slots, plus a
`PtzGuard` auto-return feature. All preset slots were empty and guard disabled
out of the box.

**Relative movements are not reversible.** Panning right 1.5s then left 1.5s at
the same speed left the camera visibly off its starting position — 33.90 mean
pixel difference against a measured noise floor of 1.66 (two still frames 3s
apart). That is 20x the noise, so it is mechanical hysteresis, not sensor
jitter.

The consequence is that framing cannot be restored by reversing a movement.
Any drift accumulates, and nothing downstream would notice: the camera keeps
delivering good frames of gradually the wrong scene.

- Use **absolute presets** (`ToPos`) to return to a known framing, never
  opposite-direction pans.
- Enable **PtzGuard** pointed at that preset, so the camera self-corrects even
  when Vision Sentinel is down.

## Configuration

RTSP and HTTP enabled; HTTPS, RTMP and ONVIF confirmed closed.

ONVIF is off deliberately: Reolink's own HTTP API already provides PTZ
position and snapshots, so ONVIF adds a listening service with no unique job.
Re-enable it only if adopting Frigate and wanting autoconfiguration.

HTTP rather than HTTPS because Reolink's HTTPS uses a self-signed certificate,
and habitually disabling certificate verification is worse hygiene than plain
HTTP on a segment that should not have internet access anyway.

The camera reports a **locally-administered (randomised) MAC**
(`0e:c7:1d:...`), so it cannot be identified by vendor OUI, and a DHCP
reservation may not survive a MAC re-randomisation.

## Gotchas

- ffmpeg's stderr embeds the full RTSP URL including credentials. Scrub before
  logging anything from the subprocess — see `sources.reolink.scrub()`.
- The `-rw_timeout` option is rejected by ffmpeg's RTSP demuxer; use `-timeout`
  (microseconds).
- `Overread VUI by N bits` warnings are benign. Reolink's H.264 SPS has a
  malformed VUI section; ffmpeg notes it and decodes correctly.
- Pan/tilt makes framing mutable state. Any crib ROI or angle-tuned threshold
  is silently invalidated by a pan, and the camera keeps producing perfectly
  good frames of the wrong thing.
