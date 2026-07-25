import { resolve } from "node:path";

export function opencrewDataDir() {
  return process.env.OPENCREW_DATA_DIR || resolve(process.env.HOME || ".", ".opencrew");
}
