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
  const normalizedText = text ?? "";
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
