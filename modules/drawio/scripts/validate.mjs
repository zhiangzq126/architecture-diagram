#!/usr/bin/env node
/**
 * DrawIO 文件自检脚本
 * 用法： node validate.mjs <file.drawio>  [<file2.drawio> ...]
 *
 * 检查项：
 *   1. XML 是否合法
 *   2. 是否存在 mxfile/diagram/mxGraphModel/root 结构
 *   3. 是否保留 mxCell id=0、id=1
 *   4. style 是否有尾分号
 *   5. value 含 HTML 标签但 style 缺 html=1
 *   6. 自闭合标签前是否有空格（如 "as=\"geometry\" />"）
 *   7. edge 缺少 source/target
 *   8. edge 没有 waypoints 且两端不在同轴线上（容易乱走）
 *   9. vertex 缺少 mxGeometry
 *
 * 仅依赖 Node 18+ 内置模块，无外部依赖。
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

const RED = "\x1b[31m";
const YELLOW = "\x1b[33m";
const GREEN = "\x1b[32m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";

const HTML_TAG_RE = /<(?:br|div|b|i|u|font|span|p)\b/i;
const TRAILING_SEMI_RE = /;"(?!\?)/; // style="...;"
const SPACE_BEFORE_SELFCLOSE_RE = / \/>/g;

function parseAttrs(tag) {
  const attrs = {};
  const re = /(\w[\w-]*)\s*=\s*"([^"]*)"/g;
  let m;
  while ((m = re.exec(tag))) attrs[m[1]] = m[2];
  return attrs;
}

function findCells(xml) {
  // 抓所有 <mxCell ...> 开标签（含自闭合）
  const re = /<mxCell\b[^>]*?\/?>/g;
  return xml.match(re) || [];
}

function findGeometriesUnderCell(xml, cellId) {
  // 粗略：定位 cell id 起，到下一个 </mxCell> 之间的内容
  const idx = xml.indexOf(`id="${cellId}"`);
  if (idx === -1) return [];
  const end = xml.indexOf("</mxCell>", idx);
  const block = end === -1 ? xml.slice(idx, idx + 500) : xml.slice(idx, end);
  return block.match(/<mxGeometry\b[^>]*?\/?>/g) || [];
}

function findPointsUnderCell(xml, cellId) {
  const idx = xml.indexOf(`id="${cellId}"`);
  if (idx === -1) return 0;
  const end = xml.indexOf("</mxCell>", idx);
  const block = end === -1 ? xml.slice(idx, idx + 500) : xml.slice(idx, end);
  return (block.match(/<mxPoint\b/g) || []).length;
}

function isFloatingEdge(xml, cellId) {
  // 浮动 edge：用 <mxPoint as="sourcePoint"/> + <mxPoint as="targetPoint"/> 取代 source/target 属性
  const idx = xml.indexOf(`id="${cellId}"`);
  if (idx === -1) return false;
  const end = xml.indexOf("</mxCell>", idx);
  const block = end === -1 ? xml.slice(idx, idx + 500) : xml.slice(idx, end);
  return (
    /<mxPoint\b[^>]*as="sourcePoint"/i.test(block) &&
    /<mxPoint\b[^>]*as="targetPoint"/i.test(block)
  );
}

function validateFile(path) {
  if (!existsSync(path)) {
    console.error(`${RED}✗ 文件不存在: ${path}${RESET}`);
    return 1;
  }

  const xml = readFileSync(path, "utf8");
  const errors = [];
  const warnings = [];

  // 1. 极简 XML 合法性：尝试用浏览器/Node 22 的 DOMParser（如可用），否则用括号配对启发
  //    这里只用启发式：标签开/闭计数
  const openTags = (xml.match(/<[a-zA-Z]/g) || []).length;
  const closeTags = (xml.match(/<\/[a-zA-Z]/g) || []).length;
  const selfClose = (xml.match(/\/>/g) || []).length;
  if (openTags !== closeTags + selfClose) {
    warnings.push(
      `标签开/闭不匹配（open=${openTags}, close=${closeTags}, selfClose=${selfClose}）—可能存在语法错误`,
    );
  }

  // 2. 结构检查
  if (!/<mxfile\b/.test(xml)) errors.push("缺少 <mxfile> 根元素");
  if (!/<diagram\b/.test(xml)) errors.push("缺少 <diagram> 元素");
  if (!/<mxGraphModel\b/.test(xml)) errors.push("缺少 <mxGraphModel> 元素");
  if (!/<root\b/.test(xml)) errors.push("缺少 <root> 元素");

  // 3. 系统保留 cell
  if (!/id="0"/.test(xml)) errors.push('缺少保留节点 <mxCell id="0"/>');
  if (!/id="1"[^>]*parent="0"/.test(xml))
    errors.push('缺少保留 layer <mxCell id="1" parent="0"/>');

  // 4. 自闭合空格
  const spaceMatches = xml.match(SPACE_BEFORE_SELFCLOSE_RE);
  if (spaceMatches && spaceMatches.length > 0) {
    warnings.push(
      `${spaceMatches.length} 处自闭合标签前有空格（应为 "/>"，而非 " />"）`,
    );
  }

  // 逐个 mxCell 校验
  const cells = findCells(xml);
  for (const cell of cells) {
    const attrs = parseAttrs(cell);
    const id = attrs.id || "(unknown)";
    if (id === "0" || id === "1") continue;

    // 5. style 尾分号
    if (attrs.style && attrs.style.endsWith(";")) {
      warnings.push(`cell id="${id}": style 以分号结尾（应去掉）`);
    }

    // 6. value 含 HTML 但 style 缺 html=1
    if (attrs.value && HTML_TAG_RE.test(attrs.value)) {
      const style = attrs.style || "";
      if (!/\bhtml=1\b/.test(style)) {
        warnings.push(
          `cell id="${id}": value 含 HTML 标签但 style 缺 html=1，HTML 不会渲染`,
        );
      }
    }

    // 7. edge 缺 source/target（浮动 edge 用 sourcePoint/targetPoint 代替，豁免）
    if (attrs.edge === "1") {
      const floating = isFloatingEdge(xml, id);
      if (!floating) {
        if (!attrs.source)
          warnings.push(`edge id="${id}": 缺少 source 属性`);
        if (!attrs.target)
          warnings.push(`edge id="${id}": 缺少 target 属性`);
        // 8. edge 无 waypoints 警告（浮动 edge 自身已有 source/targetPoint，不警告）
        const ptCount = findPointsUnderCell(xml, id);
        if (ptCount === 0) {
          warnings.push(
            `edge id="${id}": 没有 <mxPoint> waypoints，连线可能乱走（推荐显式 Manhattan 路径）`,
          );
        }
      }
    }

    // 9. vertex 缺 mxGeometry
    if (attrs.vertex === "1") {
      const geos = findGeometriesUnderCell(xml, id);
      if (geos.length === 0) {
        errors.push(`vertex id="${id}": 缺少 <mxGeometry>`);
      } else {
        const ga = parseAttrs(geos[0]);
        if (ga.width === undefined || ga.height === undefined) {
          warnings.push(`vertex id="${id}": <mxGeometry> 缺少 width/height`);
        }
      }
    }
  }

  // 输出
  console.log(`\n${DIM}── ${path} ──${RESET}`);
  if (errors.length === 0 && warnings.length === 0) {
    console.log(`${GREEN}✓ 通过（${cells.length - 2} 个用户 cell）${RESET}`);
    return 0;
  }
  for (const e of errors) console.log(`${RED}✗ ${e}${RESET}`);
  for (const w of warnings) console.log(`${YELLOW}⚠ ${w}${RESET}`);
  console.log(
    `${DIM}cell 总数=${cells.length}（含 2 个系统保留）${RESET}`,
  );
  return errors.length > 0 ? 1 : 0;
}

const files = process.argv.slice(2);
if (files.length === 0) {
  console.log("用法: node validate.mjs <file.drawio> [<file2.drawio> ...]");
  process.exit(2);
}
let exitCode = 0;
for (const f of files) exitCode |= validateFile(resolve(f));
process.exit(exitCode);
