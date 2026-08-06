import { describe, expect, it } from "vitest";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import contentRendererTemplate from "../miniprogram/pages/practice/shared/task-runtime/content-renderer/index.wxml?raw";

import {
  collectPreviewUrls,
  ContentContractError,
  contentAnchorDomId,
  prepareRichContent,
  type ContentAnchor,
  type RichContentBlock,
  type TableContentBlock,
  type TableContentCell,
} from "../miniprogram/pages/practice/shared/task-runtime/content-renderer/model";

describe("stage 2 rich content renderer contract", () => {
  it("does not introduce preserved whitespace around plain paragraph text", () => {
    expect(contentRendererTemplate).toContain(
      'class="content-paragraph" user-select>{{item.text}}</text>',
    );
  });

  it("prepares backend-native long content, readable formula, high-resolution preview and OCR source", () => {
    const blocks = complexMaterialFixture();
    const anchors = contentAnchors();
    const views = prepareRichContent(blocks, 1.25, anchors);
    const paragraph = views[0];
    const formula = views[1];
    const image = views[2];

    expect(paragraph?.kind).toBe("paragraph");
    if (paragraph?.kind !== "paragraph") throw new Error("paragraph fixture missing");
    expect(paragraph.text.length).toBeGreaterThan(3_000);
    expect(paragraph.hasHtml).toBe(true);
    expect(paragraph.anchorId).toBe("政策背景");
    expect(paragraph.domAnchorId).toBe(contentAnchorDomId("政策背景"));

    expect(formula).toMatchObject({
      kind: "formula",
      expression: "GDP=\\sum_{i=1}^{n}p_iq_i",
      format: "latex",
      displayMode: "block",
      hasRenderAsset: false,
      renderWidth: 0,
    });
    expect(image).toMatchObject({
      kind: "image",
      url: "https://cdn.example.test/material/population-preview.webp",
      originalUrl: "https://cdn.example.test/material/population-original.webp",
      originalOcrUrl: "https://cdn.example.test/material/population-ocr-original.webp",
      highResolutionWidth: 4800,
      highResolutionHeight: 3200,
    });
    expect(collectPreviewUrls(blocks)).toEqual([
      "https://cdn.example.test/material/population-original.webp",
      "https://cdn.example.test/material/trend-preview.webp",
    ]);
  });

  it("lays out an eight-column table with merged headers, frozen metadata and zoom", () => {
    const table = eightColumnTable();
    const view = prepareRichContent([table], 1.5)[0];
    if (view?.kind !== "table") throw new Error("table fixture missing");
    const columnWidths = table.columnWidths;
    if (!columnWidths) throw new Error("table width fixture missing");

    expect(view.columnCount).toBe(8);
    expect(view.tableWidth).toBe(columnWidths.reduce((sum, value) => sum + value, 0) * 1.5);
    expect(view.gridStyle).toContain("grid-template-columns:");
    expect(view.gridStyle.match(/rpx/g)?.length).toBeGreaterThanOrEqual(10);

    const mergedRegion = view.cells.find((cell) => cell.id.endsWith("cell-0-0"));
    const mergedYears = view.cells.find((cell) => cell.id.endsWith("cell-0-1"));
    expect(mergedRegion).toMatchObject({
      rowSpan: 2,
      columnSpan: 1,
      frozenColumn: true,
      frozenHeader: true,
    });
    expect(mergedRegion?.gridStyle).toContain("grid-row: 1 / span 2");
    expect(mergedYears?.gridStyle).toContain("grid-column: 2 / span 3");
    expect(view.cells.filter((cell) => cell.frozenColumn)).toHaveLength(4);
    expect(view.cells.filter((cell) => cell.frozenHeader).length).toBeGreaterThan(8);
    expect(view.cells.find((cell) => cell.id.endsWith("cell-2-1"))?.hasHtml).toBe(true);
  });

  it("uses stable default table geometry when the backend omits display dimensions", () => {
    const table = eightColumnTable();
    delete table.columnWidths;
    delete table.rowHeights;
    const view = prepareRichContent([table], 1)[0];
    if (view?.kind !== "table") throw new Error("table fixture missing");

    expect(view.columnWidths).toEqual([196, 176, 176, 176, 176, 176, 176, 176]);
    expect(view.rowHeights).toEqual([88, 88, 78, 78, 78]);
    expect(view.tableWidth).toBe(1428);
  });

  it("rejects overlapping cells, unsafe HTML and unsafe image sources", () => {
    const overlap = eightColumnTable();
    overlap.cells.push({
      row: 0,
      column: 0,
      rowSpan: 1,
      columnSpan: 1,
      text: "重叠",
      header: true,
    });
    expect(() => prepareRichContent([overlap], 1)).toThrow("重复单元格");

    const blocks = complexMaterialFixture();
    const paragraph = blocks[0];
    if (paragraph?.type !== "paragraph") throw new Error("paragraph fixture missing");
    paragraph.html = '<p onclick="steal()">不安全</p>';
    expect(() => prepareRichContent(blocks, 1)).toThrow("可执行内容");

    const image = blocks[2];
    if (image?.type !== "image") throw new Error("image fixture missing");
    image.url = "http://unsafe.example.test/original.webp";
    expect(() => prepareRichContent(blocks, 1)).toThrow(ContentContractError);
  });
});

function complexMaterialFixture(): RichContentBlock[] {
  const longText = Array.from(
    { length: 65 },
    (_, index) =>
      `第${String(index + 1)}段：本段包含统计口径、时间范围、地区范围及同比与环比的区别，用于验证长材料连续阅读时不会丢失位置。`,
  ).join("\n\n");
  return [
    {
      blockId: "policy-background",
      type: "paragraph",
      text: longText,
      html: `<p>${longText.replaceAll("\n\n", "</p><p>")}</p>`,
    },
    {
      blockId: "gdp-formula",
      type: "formula",
      expression: "GDP=\\sum_{i=1}^{n}p_iq_i",
      format: "latex",
      display: true,
    },
    {
      blockId: "population-scan",
      type: "image",
      assetId: "asset-population",
      url: "https://cdn.example.test/material/population-preview.webp",
      originalOcrUrl: "https://cdn.example.test/material/population-ocr-original.webp",
      highResolutionUrl: "https://cdn.example.test/material/population-original.webp",
      highResolutionWidth: 4800,
      highResolutionHeight: 3200,
      altText: "人口普查公报原始扫描页",
      zoomable: true,
      caption: "图 1 人口普查公报（点击查看高清原图）",
    },
    {
      blockId: "growth-trend",
      type: "graph",
      assetId: "asset-trend",
      url: "https://cdn.example.test/material/trend-preview.webp",
      originalOcrUrl: "https://cdn.example.test/material/trend-ocr-original.webp",
      altText: "2018 至 2025 年三类产业增速折线图",
      zoomable: true,
      caption: "图 2 三类产业增速",
      sourceLabel: "来源：年度统计公报",
    },
    eightColumnTable(),
  ];
}

function contentAnchors(): ContentAnchor[] {
  return [
    { anchorId: "政策背景", blockId: "policy-background", label: "政策背景" },
    { anchorId: "regional-table", blockId: "regional-table", label: "分地区数据表" },
  ];
}

function eightColumnTable(): TableContentBlock {
  const cells: TableContentCell[] = [
    {
      row: 0,
      column: 0,
      rowSpan: 2,
      columnSpan: 1,
      text: "地区",
      header: true,
    },
    {
      row: 0,
      column: 1,
      rowSpan: 1,
      columnSpan: 3,
      text: "常住人口（万人）",
      header: true,
    },
    {
      row: 0,
      column: 4,
      rowSpan: 1,
      columnSpan: 4,
      text: "经济结构",
      header: true,
    },
  ];
  const detailLabels = ["2023", "2024", "增速", "第一产业", "第二产业", "第三产业", "人均 GDP"];
  for (let column = 1; column < 8; column += 1) {
    const text = detailLabels[column - 1];
    if (!text) throw new Error("detail label fixture missing");
    cells.push({
      row: 1,
      column,
      rowSpan: 1,
      columnSpan: 1,
      text,
      header: true,
    });
  }
  const regions = ["甲省", "乙省", "丙省"];
  for (let row = 2; row < 5; row += 1) {
    for (let column = 0; column < 8; column += 1) {
      const region = regions[row - 2];
      if (!region) throw new Error("region fixture missing");
      const text = column === 0 ? region : String(row * 100 + column);
      cells.push({
        row,
        column,
        rowSpan: 1,
        columnSpan: 1,
        text,
        ...(row === 2 && column === 1 ? { html: `<strong>${text}</strong>` } : {}),
        header: false,
        align: column === 0 ? "left" : "right",
      });
    }
  }
  return {
    blockId: "regional-table",
    type: "table",
    caption: "表 1 分地区人口与经济结构（八列合并表头）",
    columnCount: 8,
    rowCount: 5,
    frozenHeaderRows: 2,
    horizontallyScrollable: true,
    columnWidths: [196, 172, 172, 152, 172, 172, 172, 210],
    rowHeights: [96, 84, 78, 78, 78],
    cells,
  };
}
