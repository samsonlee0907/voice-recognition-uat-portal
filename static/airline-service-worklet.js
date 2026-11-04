"use strict";

class AirlineServiceProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 24000;
    this.micSum = this.referenceSum = this.weight = 0;
    this.buffer = new Int16Array(1920);
    this.position = 0;
    this.microphoneMuted = false;
    this.port.onmessage = event => {
      if (event.data?.type !== "microphone_gate") return;
      this.microphoneMuted = event.data.muted === true;
      if (this.microphoneMuted) {
        this.micSum = 0;
        for (let index = 0; index < this.position; index += 2) this.buffer[index] = 0;
      }
    };
  }
  process(inputs) {
    const mic = inputs[0]?.[0];
    const reference = inputs[1]?.[0];
    const length = mic?.length || reference?.length || 128;
    for (let index = 0; index < length; index++) {
      let remaining = 1;
      while (remaining > 1e-9) {
        const amount = Math.min(remaining, this.ratio - this.weight);
        this.micSum += (this.microphoneMuted ? 0 : mic?.[index] || 0) * amount;
        this.referenceSum += (reference?.[index] || 0) * amount;
        this.weight += amount;
        remaining -= amount;
        if (this.weight >= this.ratio - 1e-9) {
          for (const value of [this.micSum / this.weight, this.referenceSum / this.weight]) {
            const bounded = Math.max(-1, Math.min(1, value));
            this.buffer[this.position++] = Math.round(bounded * (bounded < 0 ? 32768 : 32767));
          }
          this.micSum = this.referenceSum = this.weight = 0;
          if (this.position === this.buffer.length) {
            this.port.postMessage(this.buffer.buffer, [this.buffer.buffer]);
            this.buffer = new Int16Array(1920);
            this.position = 0;
          }
        }
      }
    }
    return true;
  }
}
registerProcessor("airline-service-processor", AirlineServiceProcessor);
