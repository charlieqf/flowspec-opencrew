let sharedAudioContext = null;

export function getSharedAudioContext() {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return null;
  if (!sharedAudioContext) sharedAudioContext = new AudioCtx();
  return sharedAudioContext;
}
