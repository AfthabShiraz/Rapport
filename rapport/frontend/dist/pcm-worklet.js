// Mic Float32 (native rate) -> 24 kHz mono PCM16, posted in ~100ms chunks.
const TARGET_RATE = 24000
const CHUNK = 2400 // samples = 100ms @ 24k

class PCMDownsampler extends AudioWorkletProcessor {
  constructor() {
    super()
    this.step = sampleRate / TARGET_RATE
    this.nextPos = 0 // position (in input samples) of the next output sample
    this.inPos = 0 // absolute index of the current input sample
    this.prev = 0
    this.out = new Int16Array(CHUNK)
    this.outLen = 0
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) {
      const cur = ch[i]
      while (this.nextPos <= this.inPos) {
        const frac = this.nextPos - (this.inPos - 1)
        const v = frac <= 0 ? this.prev : this.prev + (cur - this.prev) * frac
        const s = Math.max(-1, Math.min(1, v))
        this.out[this.outLen++] = s < 0 ? s * 0x8000 : s * 0x7fff
        if (this.outLen === CHUNK) {
          this.port.postMessage(this.out.buffer.slice(0))
          this.outLen = 0
        }
        this.nextPos += this.step
      }
      this.prev = cur
      this.inPos += 1
    }
    return true
  }
}

registerProcessor('pcm-downsampler', PCMDownsampler)
