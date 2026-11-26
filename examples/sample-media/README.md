# Sample media

`demo.mp4` is fully synthetic (FFmpeg test pattern and a 440 Hz tone, no camera, no people),
10 s, 320x240 at 15 fps, H.264 + mono AAC. Regenerate it with:

```bash
ffmpeg -y -f lavfi -i "testsrc2=size=320x240:rate=15:duration=10" \
  -f lavfi -i "sine=frequency=440:sample_rate=16000:duration=10" \
  -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p -g 30 \
  -c:a aac -b:a 32k -ac 1 -shortest -movflags +faststart -map_metadata -1 demo.mp4
```
