import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const manuals = [
  {
    source: "media-library-r2-manual.html",
    output: "OpenCrew_素材库标签_卡片视图与时间轴拖动_单文件手册.html",
    expectedImages: 8,
  },
  {
    source: "gemini-omni-manual.html",
    output: "OpenCrew_Gemini_Omni_有状态视频编辑_单文件手册.html",
    expectedImages: 15,
  },
];

for (const manual of manuals) {
  let html = await readFile(path.join(root, manual.source), "utf8");
  let embeddedImages = 0;
  const matches = [...html.matchAll(/src="assets\/([^"]+)"/g)];
  for (const match of matches) {
    const filename = match[1];
    const image = await readFile(path.join(root, "assets", filename));
    const dataUri = `data:image/png;base64,${image.toString("base64")}`;
    html = html.replace(match[0], `src="${dataUri}"`);
    embeddedImages += 1;
  }
  if (embeddedImages !== manual.expectedImages) {
    throw new Error(`${manual.source}: expected ${manual.expectedImages} images, embedded ${embeddedImages}`);
  }
  html = html
    .replace("<body>", '<body id="top">')
    .replace(/href="index\.html"/g, 'href="#top"')
    .replace("</title>", "（单文件离线版）</title>")
    .replace(
      "</head>",
      "  <!-- Self-contained offline edition: all screenshots and CSS are embedded. -->\n</head>",
    );
  if (/src="assets\//.test(html) || /href="index\.html"/.test(html)) {
    throw new Error(`${manual.output}: local dependency remained after embedding`);
  }
  await writeFile(path.join(root, manual.output), html, "utf8");
  console.log(`${manual.output}: ${embeddedImages} images embedded`);
}
