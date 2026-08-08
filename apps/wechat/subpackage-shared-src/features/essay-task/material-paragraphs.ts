export interface MaterialTextParagraph {
  paragraphId: string;
  text: string;
  displayText: string;
  paragraphIndex: number;
}

export function materialTextParagraphs(
  text: string | null | undefined,
  sourceId: string,
): MaterialTextParagraph[] {
  const normalizedText = repairKnownEssayMaterialTypos(text ?? "");
  const paragraphs = normalizedText
    .split(/\r?\n+/u)
    .map((item) => item.trim())
    .filter(Boolean);
  const source = paragraphs.length > 0 ? paragraphs : [normalizedText.trim()].filter(Boolean);
  return source.map((paragraph, index) => ({
    paragraphId: `${sourceId}-paragraph-${String(index + 1)}`,
    text: paragraph,
    displayText: `\u3000\u3000${paragraph}`,
    paragraphIndex: index + 1,
  }));
}

/**
 * 只修复已经与同题正确版本对照过的历史录入错字。
 * 不做泛化 OCR 替换，避免将“十几平方米”等正常文本误改。
 */
export function repairKnownEssayMaterialTypos(text: string): string {
  return text
    .replaceAll("淋浴系统大理石台面等几平是", "淋浴系统、大理石台面等几乎是")
    .replaceAll("掉色的相子", "掉色的箱子");
}
